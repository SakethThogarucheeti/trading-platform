# broker/base/ (removed)

The `broker/base/` directory previously held the `Broker` and `BrokerStream` abstract base classes as standalone files. Those are now in the module's service layer:

| Old path | New path |
|----------|----------|
| `broker/base/broker.py` | `broker/service/broker.py` |
| `broker/base/broker_stream.py` | `broker/service/broker_stream.py` |

Public consumers should import from the module API:

```python
from trading.broker.api import Broker, BrokerStream
```
