# core/lifecycle/

Async component base class and runtime supervisor. Add new long-running service base classes here.

## Files

| File | Purpose |
|------|---------|
| `component.py` | `Component` ABC with a five-state lifecycle |
| `runtime.py` | `Runtime` supervisor — ordered startup, reverse shutdown |

## Component lifecycle

```
CREATED → STARTING → RUNNING → STOPPING → STOPPED
```

Subclasses override two methods:

- `_setup()` — async setup that completes before the component is considered RUNNING (e.g., connect to broker, subscribe to channels). The runtime calls `TaskGroup.start()` on each component and waits for `_setup()` to finish before moving to the next.
- `_run()` — async body that blocks until cancelled (e.g., `sleep_forever()`, receive loop). Cancellation triggers the STOPPING → STOPPED transition.

## Runtime

`Runtime` owns an ordered list of components. On `start()`, it starts them sequentially (each `_setup()` must complete before the next component begins). On `stop()`, it cancels the internal scope, which unwinds components in reverse order — the last component started is the first to stop.

`AbstractRuntime` is the interface registered in the DI container; `Runtime` is the concrete implementation.

External stop signals (e.g., from the scheduler at 15:30) are delivered via `call_soon_threadsafe` to safely cross the thread boundary.

## Relationship to other packages

- `tick_ingest/`, `candles/`, `worker/`, `monitoring/`, `api/dashboard/` — all provide `Component` subclasses
- `di/providers/components.py` and `di/providers/worker_components.py` — assemble and order the component list, then hand it to `Runtime`
