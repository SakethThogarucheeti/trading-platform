# walk_forward/

Walk-forward analysis engine.

Splits a date range into rolling in-sample / out-of-sample windows, fits strategy parameters on the in-sample window using a parameter grid, and validates on the out-of-sample window. Aggregates per-fold metrics into a combined out-of-sample equity curve.

## Key classes

- **`WalkForwardSplit`** — generates `(in_sample_start, in_sample_end, oos_start, oos_end)` tuples for a given date range and window sizes.
- **`WalkForwardAggregator`** — runs `BacktestSession` for each fold, collects results, and produces a final report with per-fold and aggregate metrics.

Used by `tst/integ/strategy/strategies/test_walk_forward.py`.
