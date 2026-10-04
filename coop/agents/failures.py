"""Stop a batch when the model driver fails.

The simulator records an API or driver failure as ``driver_error`` and sets
``meta.status`` to ``driver_failure``. ``action_rejected`` is an invalid
action, not a failed call, so it does not stop the batch. This module does
not edit the simulator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def failure_reason(events: list[dict[str, Any]], meta: dict[str, Any]) -> str | None:
    """Why this run should stop the batch, or None if it may continue.

    A budget abort is left to the budget guard. Any ``driver_error`` event
    stops the batch, as does a status other than ``complete``.
    """
    status = meta.get("status")
    if status not in (None, "complete", "aborted_budget"):
        return f"run status is {status!r}, not complete"
    driver_errors = [event for event in events if event.get("type") == "driver_error"]
    if driver_errors:
        return f"{len(driver_errors)} driver_error event(s)"
    return None


def mark_run_invalid(run_dir: Path, meta: dict[str, Any], reason: str) -> None:
    """Record that this run cannot be used. Keeps ``driver_failure`` if set."""
    meta["valid"] = False
    meta["invalid_reason"] = reason
    if meta.get("status") in (None, "complete"):
        meta["status"] = "invalid"
    meta["aborted"] = reason
    meta["abort_reason"] = reason
    path = run_dir / "meta.json"
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
