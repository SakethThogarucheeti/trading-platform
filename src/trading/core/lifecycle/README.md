# core/lifecycle

Structured concurrency primitives for long-running components.

## Files

**`component.py`** — `Component` ABC. Subclasses implement `_setup()` and `_run()`, and optionally `_teardown()`. The `start()` coroutine runs them in order; `stop()` cancels `_run` and awaits `_teardown`. State machine: `IDLE → STARTING → RUNNING → STOPPING → STOPPED`.

**`runtime.py`** — `Runtime` supervisor. Takes an ordered list of `Component` instances, starts them sequentially (each `_setup` completes before the next begins), runs all `_run` coroutines concurrently, and tears them down in reverse order on cancellation.

## Usage

```python
class MyWorker(Component):
    async def _setup(self) -> None:
        await self._store.load()

    async def _run(self) -> None:
        await sleep_forever()

    async def _teardown(self) -> None:
        await self._store.flush()

runtime = Runtime([component_a, component_b])
await runtime.start()   # blocks; cancel the task to stop
```
