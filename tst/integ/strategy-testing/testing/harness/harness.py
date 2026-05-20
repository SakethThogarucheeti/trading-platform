from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from testing.registry import build_session, config_types
from testing.report_base import SessionReport
from testing.session import SessionConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HarnessConfig
# ---------------------------------------------------------------------------


@dataclass
class HarnessConfig:
    """
    Top-level configuration for a test harness run.

    ``testing_configs`` is a homogeneous list of ``SessionConfig`` instances
    of any mix of registered session types. The harness dispatches each via
    the registry — no hardcoded type list.

    Assertions
    ----------
    Checked after all sessions complete. Keys match assertion function names
    in ``utils.assertions``. Currently supported:

    - ``min_sharpe``       — minimum allowed Sharpe ratio (backtest sessions)
    - ``max_drawdown``     — maximum allowed drawdown fraction (backtest)
    - ``min_win_rate``     — minimum allowed win rate fraction (backtest)
    - ``max_ruin_probability`` — maximum allowed ruin probability (monte_carlo)
    """

    name: str
    testing_configs: list[SessionConfig] = field(default_factory=list)
    assertions: dict[str, float] = field(default_factory=dict)
    results_dir: Path = field(default_factory=lambda: Path("session_results"))

    @classmethod
    def from_yaml(cls, path: Path) -> HarnessConfig:
        """
        Parse a YAML harness config file.

        The ``type`` field of each entry in ``testing_configs`` is used to
        look up the correct config class in the session registry. New session
        types are picked up automatically — no code changes needed here.
        """
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls._from_dict(raw)

    @classmethod
    def from_json(cls, path: Path) -> HarnessConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> HarnessConfig:
        type_map = {c.type: c for c in config_types()}  # type: ignore[attr-defined]
        configs: list[SessionConfig] = []

        for item in raw.get("testing_configs", []):
            type_name = item.get("type")
            config_cls = type_map.get(type_name)
            if config_cls is None:
                raise ValueError(
                    f"HarnessConfig: unknown session type {type_name!r}. "
                    f"Registered types: {list(type_map.keys())}"
                )
            configs.append(_deserialize_config(config_cls, item))

        return cls(
            name=raw["name"],
            testing_configs=configs,
            assertions=raw.get("assertions", {}),
            results_dir=Path(raw.get("results_dir", "session_results")),
        )


# ---------------------------------------------------------------------------
# TraderReport
# ---------------------------------------------------------------------------


@dataclass
class TraderReport(SessionReport):
    """
    Aggregated report covering all sessions in a harness run.

    ``to_html()`` embeds each sub-report's HTML in a collapsible section
    without any ``isinstance`` checks — purely polymorphic via ``to_html()``.
    """

    harness_name: str
    reports: dict[str, SessionReport]  # key = session_id
    passed: bool
    failures: list[str]
    run_at: datetime

    # SessionReport fields
    session_id: str = ""
    session_type: str = "trader_report"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "harness_name": self.harness_name,
            "run_at": self.run_at.isoformat(),
            "passed": self.passed,
            "failures": self.failures,
            "session_count": len(self.reports),
            "sessions": {sid: r.to_dict() for sid, r in self.reports.items()},
        }

    def to_html(self) -> str:
        status_badge = (
            '<span style="color:#4CAF50;font-weight:bold">PASSED</span>'
            if self.passed
            else '<span style="color:#F44336;font-weight:bold">FAILED</span>'
        )

        failures_html = ""
        if self.failures:
            items = "".join(f"<li>{f}</li>" for f in self.failures)
            failures_html = f"<h2>Failures</h2><ul>{items}</ul>"

        sections: list[str] = []
        for sid, report in self.reports.items():
            try:
                inner_html = report.to_html()
                body_start = inner_html.find("<body>")
                body_end = inner_html.find("</body>")
                if body_start >= 0 and body_end >= 0:
                    inner_content = inner_html[body_start + 6 : body_end]
                else:
                    inner_content = inner_html
            except Exception as exc:
                inner_content = f"<p>Error rendering report: {exc}</p>"

            sections.append(f"""
<details>
  <summary><strong>{report.session_type}</strong> — {sid}</summary>
  <div style="margin-left:20px">
    {inner_content}
  </div>
</details>""")

        sections_html = "\n".join(sections)

        return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Trader Report — {self.harness_name}</title>
  <style>
    body{{font-family:sans-serif;margin:20px}}
    details{{border:1px solid #ddd;border-radius:4px;margin:8px 0;padding:8px}}
    summary{{cursor:pointer;font-size:1.1em}}
  </style>
</head>
<body>
  <h1>Trader Report — {self.harness_name}</h1>
  <p><strong>Status:</strong> {status_badge} &nbsp;
     <strong>Run at:</strong> {self.run_at.strftime("%Y-%m-%d %H:%M UTC")} &nbsp;
     <strong>Sessions:</strong> {len(self.reports)}</p>
  {failures_html}
  <h2>Sessions</h2>
  {sections_html}
</body>
</html>"""


# ---------------------------------------------------------------------------
# TestHarness
# ---------------------------------------------------------------------------


class TestHarness:
    """
    Orchestrate multiple testing sessions and produce a ``TraderReport``.

    ``run()`` contains no ``isinstance`` checks and no hardcoded session type
    names. It calls ``build_session(cfg, ...)`` which dispatches via the
    registry. Adding a new session type requires zero changes here.
    """

    def __init__(
        self,
        config: HarnessConfig,
        db_engine: AsyncEngine,
    ) -> None:
        self._config = config
        self._db_engine = db_engine

    async def run(self) -> TraderReport:
        config = self._config
        config.results_dir.mkdir(parents=True, exist_ok=True)
        run_at = datetime.now(UTC)

        reports: dict[str, SessionReport] = {}

        for cfg in config.testing_configs:
            logger.info(
                "TestHarness: starting session type=%r id=%r",
                cfg.type,
                cfg.session_id or "(auto)",
            )
            session = build_session(
                cfg,
                db_engine=self._db_engine,
                results_dir=config.results_dir,
            )
            report = await session.run()
            reports[report.session_id] = report
            logger.info("TestHarness: session %r complete", report.session_id)

        failures = self._evaluate_assertions(reports)

        trader_report = TraderReport(
            harness_name=config.name,
            reports=reports,
            passed=not failures,
            failures=failures,
            run_at=run_at,
            session_id=str(uuid.uuid4()),
            session_type="trader_report",
            started_at=run_at,
            finished_at=datetime.now(UTC),
        )

        # Save the trader report
        report_dir = config.results_dir / config.name
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = run_at.strftime("%Y%m%dT%H%M%S")
        trader_report.save(report_dir / timestamp)

        return trader_report

    def _evaluate_assertions(self, reports: dict[str, SessionReport]) -> list[str]:
        """
        Evaluate harness-level assertions against the completed reports.

        Returns a list of failure messages. Empty list means all assertions pass.
        """
        failures: list[str] = []
        assertions = self._config.assertions

        for sid, report in reports.items():
            label = f"[{report.session_type}:{sid[:8]}]"

            if "min_sharpe" in assertions and hasattr(report, "sharpe_ratio"):
                threshold = assertions["min_sharpe"]
                val = report.sharpe_ratio  # type: ignore[attr-defined]
                if val < threshold:
                    failures.append(f"{label} Sharpe {val:.3f} < min_sharpe {threshold:.3f}")

            if "max_drawdown" in assertions and hasattr(report, "max_drawdown"):
                threshold = assertions["max_drawdown"]
                val = report.max_drawdown  # type: ignore[attr-defined]
                if val > threshold:
                    failures.append(f"{label} drawdown {val:.2%} > max_drawdown {threshold:.2%}")

            if "min_win_rate" in assertions and hasattr(report, "win_rate"):
                threshold = assertions["min_win_rate"]
                val = report.win_rate  # type: ignore[attr-defined]
                if val < threshold:
                    failures.append(f"{label} win_rate {val:.2%} < min_win_rate {threshold:.2%}")

            if "max_ruin_probability" in assertions and hasattr(report, "probability_of_ruin"):
                threshold = assertions["max_ruin_probability"]
                val = report.probability_of_ruin  # type: ignore[attr-defined]
                if val > threshold:
                    failures.append(
                        f"{label} ruin_prob {val:.2%} > max_ruin_probability {threshold:.2%}"
                    )

        return failures


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deserialize_config(config_cls: type, data: dict[str, Any]) -> SessionConfig:
    """
    Deserialise a raw dict into a config class instance.

    Uses dataclass field introspection so no special-casing per config type.
    Skips unknown keys silently (forward compatibility).
    """
    import dataclasses

    if not dataclasses.is_dataclass(config_cls):
        return config_cls(**data)

    fields = {f.name for f in dataclasses.fields(config_cls)}
    known = {k: v for k, v in data.items() if k in fields and k != "type"}
    return config_cls(**known)
