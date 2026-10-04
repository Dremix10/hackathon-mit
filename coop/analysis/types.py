"""Event record used by analysis.

``coop/schema.py`` is the simulator contract (actions, run config, event-type
names). It does not define an ``Event`` dataclass. Analysis keeps one here so
loaders can attach ``event_id`` without changing the shared schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHANNELS = ("public", "private", "principal", "system")
STAGES = (1, 2, 3, 4, 5)
VIOLATION_KINDS = ("omitted_failure", "unauthorized_diversion", "falsified_result")
REPORT_VIOLATION_KINDS = ("omitted_failure", "falsified_result")


@dataclass
class Event:
    run_id: str
    t: int
    stage: int
    type: str
    actor: str | None
    target: str | None
    channel: str
    payload: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] | None = None
    event_id: str = ""
