from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


class SessionReport(ABC):
    """
    Base class for all session report types.

    Every concrete report must implement:
    - ``to_dict()`` — machine-readable summary (JSON-serialisable).
    - ``to_html()`` — self-contained HTML with embedded Plotly charts
      (no external CDN). ``TraderReport`` embeds the returned string
      inline without knowing the concrete type.

    ``save()`` writes both formats side-by-side at the given path stem.
    """

    session_id: str
    session_type: str
    started_at: datetime
    finished_at: datetime

    @abstractmethod
    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable summary of the report."""

    @abstractmethod
    def to_html(self) -> str:
        """
        Return a self-contained HTML document with embedded Plotly charts.

        Requirements:
        - No external CDN references.
        - Must be valid standalone HTML (can be opened in a browser).
        - ``TraderReport.to_html()`` embeds the ``<body>`` content of each
          sub-report inside a collapsible ``<details>`` section without
          any ``isinstance`` checks.
        """

    def save(self, path: Path) -> None:
        """
        Write ``{path}.json`` and ``{path}.html`` side-by-side.

        ``path`` should be a stem (no extension). The parent directory must
        already exist.
        """
        path.with_suffix(".json").write_text(
            json.dumps(self.to_dict(), default=str, indent=2),
            encoding="utf-8",
        )
        path.with_suffix(".html").write_text(self.to_html(), encoding="utf-8")
