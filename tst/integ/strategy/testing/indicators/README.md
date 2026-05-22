# indicators/

Indicator testing harness — utilities for fetching candle data and asserting indicator output properties.

Provides helpers used by `tst/integ/strategy/indicators/` tests:
- Fetching N bars of a given symbol/interval from Postgres
- Running an indicator and asserting it produces non-null values after warmup
- Comparing indicator values against a reference implementation
