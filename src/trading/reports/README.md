# reports

PnL calculation and trade report generation.

## Layout

```
reports/
├── api/
│   ├── __init__.py       Re-exports: ReportEngine
│   └── interfaces.py     AbstractTradingStore, AbstractPositionStore protocols
├── service/
│   (no service layer — logic lives directly in engine.py / pnl.py / trades.py)
├── di/
│   └── providers.py      ReportsProvider
├── engine.py             ReportEngine — orchestrates fetch + render
├── fetch.py              DB queries for trade history and positions
├── pnl.py                PnL calculation (realized, unrealized, daily breakdown)
├── trades.py             Trade-level aggregation and filtering
└── render.py             Console/Markdown rendering helpers
```

## What it does

`ReportEngine` queries `TradingStore` and `PositionStore` (injected via `AbstractTradingStore` / `AbstractPositionStore` protocols) and builds structured report objects. The `scripts/report.py` CLI calls these and renders to stdout.

## Imports

```python
from trading.reports.api import ReportEngine
```
