"""The status line the viewer's agent panel reads, and the run's price list."""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from ..pricing import pricing_payload
from ..tracelog import AgentTrace

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StatusLine:
    """The five strings the viewer's status channel carries.

    Frozen and named because it is also compared against the last one sent, and
    a five-slot tuple said nothing about which slot was which.
    """

    mode: str = ""
    brief: str = ""
    thought: str = ""
    detail_json: str = ""
    cost_json: str = ""

    def as_arguments(self) -> tuple[str, str, str, str, str]:
        """The positional arguments `WorldClient.report_status` takes."""
        return (
            self.mode,
            self.brief,
            self.thought,
            self.detail_json,
            self.cost_json,
        )


def _write_pricing(trace: AgentTrace, planner_model: str) -> None:
    """Record the prices this run is billed at, next to the other traces.

    Written once at startup so a later analysis of the run uses the price in
    force then rather than today's constant (docs/11_cost_accounting.md). A
    disabled trace writes no files at all, and a file that cannot be written
    complains rather than taking the actor down.
    """
    if not trace.enabled:
        return
    path = trace.pricing_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(pricing_payload(planner_model), indent=2), encoding="utf-8"
        )
    except OSError as error:
        logger.warning("pricing_write_failed", path=str(path), error=str(error))
