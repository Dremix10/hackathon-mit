"""Append-only ``research/record.jsonl`` lines.

Blind predictions are written here before ``sealed.json`` is opened.
The planner also writes the confirmatory test spec here before a batch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coop.eval.contract import PRIMARY_ACCESS, PRIMARY_VISIBILITY, PROTOCOL_VERSION

DEFAULT_RECORD_PATH = Path("research/record.jsonl")

# The two confirmatory tests. Order is binding (protocol §8).
PREREGISTERED_TESTS = (
    {
        "name": "recruiter_vs_null",
        "method": "fisher_exact",
        "alternative": "two-sided",
        "arms": ["recruiter", "null_insider"],
        "outcome": "Y",
    },
    {
        "name": "null_vs_pressure_only",
        "method": "fisher_exact",
        "alternative": "two-sided",
        "arms": ["null_insider", "pressure_only"],
        "outcome": "Y",
    },
)

SECONDARY_TESTS = ("recruiter_vs_pressure_only",)


def append_record(path: Path, entry: dict) -> dict:
    """Append one JSON object. ``ts`` is added when the caller omits it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = dict(entry)
    line.setdefault("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")
    return line


def log_preregistered_tests(path: Path | None = None) -> dict:
    """Write the §8 confirmatory test spec. Call this before the batch runs."""
    entry = {
        "kind": "preregistered_tests",
        "protocol_version": PROTOCOL_VERSION,
        "cell": {"access": PRIMARY_ACCESS, "visibility": PRIMARY_VISIBILITY},
        "tests": [dict(item) for item in PREREGISTERED_TESTS],
        "secondary": list(SECONDARY_TESTS),
        "note": "Logged before the batch. Only these two Fisher tests are confirmatory.",
    }
    return append_record(Path(path) if path is not None else DEFAULT_RECORD_PATH, entry)


def log_blind_prediction(path: Path, run_id: str, predicted_objective: str) -> dict:
    """Timestamped prediction. Must be called before sealed.json is read."""
    return append_record(
        path,
        {
            "kind": "blind_prediction",
            "protocol_version": PROTOCOL_VERSION,
            "run_id": run_id,
            "predicted_objective": predicted_objective,
        },
    )
