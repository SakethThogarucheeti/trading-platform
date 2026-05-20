"""
Per-tick trace context.

A new thread_id is assigned in KiteIngestor._handle_tick() for each WebSocket
invocation and stored here.  The logging filter in main.py injects it into
every LogRecord so every log line emitted anywhere in the pipeline carries
[thread_id] automatically, without callers needing to pass it explicitly.
"""

from __future__ import annotations

import contextvars

# 8-char hex string, e.g. "a3f2c1b8".  "--------" = outside a tick context.
thread_id: contextvars.ContextVar[str] = contextvars.ContextVar("thread_id", default="--------")
