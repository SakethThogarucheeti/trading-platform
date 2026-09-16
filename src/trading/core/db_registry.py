from __future__ import annotations

from sqlalchemy.orm import registry

# trading-platform#35/#103: every storage module used to define its own independent
# DeclarativeBase, each with its own registry/MetaData. A string-based relationship()
# or ForeignKey() can only resolve against tables registered in the *same* registry,
# so cross-module references (e.g. execution.storage.models.Order -> strategy's
# signals table) couldn't be expressed at all -- NoReferencedTableError at
# mapper-configure time. Sharing one registry across all per-module Base classes
# lets those references resolve while keeping models organized under their owning
# module (the user's Option 1 pick over reverting to one monolithic core/models.py).
shared_registry = registry()
