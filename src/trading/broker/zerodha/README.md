# broker/zerodha/ (removed)

The `broker/zerodha/` directory previously held the Zerodha implementation files. Those are now in the module's service layer:

| Old path | New path |
|----------|----------|
| `broker/zerodha/broker.py` | `broker/service/zerodha/broker.py` |
| `broker/zerodha/kite_client.py` | `broker/service/zerodha/kite_client.py` |
| `broker/zerodha/models.py` | `broker/service/zerodha/models.py` |
| `broker/zerodha/stream.py` | `broker/service/zerodha/stream.py` |

Public consumers should import from the module API:

```python
from trading.broker.api import Broker, BrokerStream
```
