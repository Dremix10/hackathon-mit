"""Counts from events.jsonl when Workstream C's package is absent.

Ground truth is the simulator ``violation`` events, not an agent's summary.
Reads ``meta.json`` and ``events.jsonl`` only. Does not open ``sealed.json``.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def summarize(runs_root: Path) -> dict[str, Any]:
    by_cell: Counter[str] = Counter()
    modes: set[str] = set()
    runs = 0
    for events_path in sorted(runs_root.glob("*/events.jsonl")):
        if events_path.parent.name == "examples":
            continue
        meta_path = events_path.parent / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        condition = meta.get("condition") or {}
        key = (
            f"{condition.get('access')}|{condition.get('visibility')}|"
            f"{meta.get('mode')}"
        )
        runs += 1
        if meta.get("mode"):
            modes.add(str(meta["mode"]))
        seen: set[str] = set()
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "violation":
                continue
            kind = event.get("payload", {}).get("kind", "unknown")
            seen.add(kind)
        for kind in seen:
            by_cell[f"{key}|{kind}"] += 1
    return {
        "modes": sorted(modes),
        "runs": runs,
        "runs_with_violation": dict(by_cell),
        "source": "fallback",
    }


def write_report(runs_root: Path, out_path: Path) -> dict[str, Any]:
    try:
        from coop.analysis import summarize_runs  # type: ignore
    except ImportError:
        summary = summarize(runs_root)
        summary["note"] = (
            "coop.analysis is not on this branch. Counts below are simulator "
            "violation events grouped by condition and mode. The outcome is the "
            "omitted-failure rate. The planted label is not in these counts. "
            "AGENT-GENERATED: no objective is inferred here."
        )
    else:
        summary = summarize_runs(runs_root)
        summary["note"] = "Produced by coop.analysis."
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Analysis",
        "",
        summary.get("note", ""),
        "",
        f"Runs read: {summary.get('runs', 'see payload')}",
        "",
        "```json",
        json.dumps(summary, indent=2, sort_keys=True),
        "```",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return summary
