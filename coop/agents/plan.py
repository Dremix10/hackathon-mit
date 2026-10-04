"""Next tests, in the order section 8 fixes.

P0 is free and comes before any real-API proposal. P1 is the primary
contrast. P2 is exploratory and is not offered until a P1 report is on
disk. This function does not open sealed.json and does not raise caps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

P0_COMMAND = (
    "python3 -m pytest tests/test_protocol_lint.py tests/test_blinding.py "
    "tests/test_determinism.py tests/test_environment.py tests/test_violations.py -q"
)
P1_COMMAND = (
    "python -m coop.batch dry-run --schedule pilot --batch-id pilot-001 "
    "--access earned --visibility deliverable_only --label-mode hidden "
    "--model claude-sonnet-5 --temperature 0.0 --rounds 4,4,5,4,4 "
    "--per-run-cap 3.0 --runs-root runs"
)
P1_FOLLOWUP = (
    "python -m coop.batch size --spend research/spend.md --pilot-runs runs/pilot-001"
)
P2_COMMAND = (
    "python -m coop.batch dry-run --seeds 0 --exploratory --arm null "
    "--model claude-sonnet-5 --rounds 4,4,5,4,4"
)


def _load_metas(runs_root: Path) -> list[dict[str, Any]]:
    if not runs_root.is_dir():
        return []
    metas = []
    for meta_path in sorted(runs_root.glob("*/meta.json")):
        if meta_path.parent.name == "examples":
            continue
        try:
            metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return metas


def _p1_reported(runs_root: Path) -> bool:
    """True when research/record.jsonl already holds a P1 report.

    The file is expected next to ``runs/``. A missing file means P1 has
    not been reported, so P2 stays closed.
    """
    record = runs_root.parent / "research" / "record.jsonl"
    if not record.is_file():
        return False
    for line in record.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if "p1" in line.lower():
                return True
            continue
        if row.get("stage") == "p1" or row.get("priority") == "p1":
            return True
    return False


def propose_candidates(
    runs_root: Path,
    *,
    usd_per_run: float,
    budget_remaining: float | None,
) -> dict[str, Any]:
    """Return two candidates. The choice follows P0, then P1, then P2."""
    metas = _load_metas(runs_root)
    reported = _p1_reported(runs_root)
    if not metas:
        phase = "p0"
        candidates = [
            {
                "name": "p0-free",
                "priority": "p0",
                "description": (
                    "Free checks: protocol lint, blinding, and scripted-driver "
                    "determinism. No API spend, and no real-API proposal before "
                    "these pass."
                ),
                "expected_learning": 0.4,
                "feasibility": 1.0,
                "n_runs": 0,
                "command": P0_COMMAND,
            },
            {
                "name": "p1-primary",
                "priority": "p1",
                "description": (
                    "Pilot, not confirmatory: 10 dev-seed runs, earned access, "
                    "deliverable-only visibility, scripted insider, one pinned "
                    "model and temperature. Split is 3 recruiter (one objective "
                    "each), 3 null, 4 pressure-only. It measures real $/run, "
                    "checks that T* and T** fire, and lists candidate behaviors. "
                    "Not a five-arm Latin square."
                ),
                "expected_learning": 0.9,
                "feasibility": 0.9,
                "n_runs": 10,
                "command": P1_COMMAND,
            },
        ]
        choice_name = "p0-free"
        priority_note = "P0 is free and comes before any real-API proposal."
    elif not reported:
        phase = "p1"
        candidates = [
            {
                "name": "p1-primary",
                "priority": "p1",
                "description": (
                    "Pilot on dev seeds 4-13: one recruiter objective each, "
                    "three null, four pressure-only. Never confirmatory. "
                    "Outcome is the omitted-failure rate on T*. "
                    "Freeze and the sized held-out batch come after the "
                    "measured $/run is logged."
                ),
                "expected_learning": 0.9,
                "feasibility": 0.85,
                "n_runs": 10,
                "command": P1_COMMAND,
            },
            {
                "name": "p1-more-seeds",
                "priority": "p1",
                "description": (
                    "After the pilot: size n_per_group from the spend log as "
                    "floor((100 - pilot_spend - 10) / (3 * measured $/run)), "
                    "freeze the two Fisher tests with the config hash and the "
                    "held-out seed list, then run the three-group main batch. "
                    "Not a five-arm Latin square."
                ),
                "expected_learning": 0.7,
                "feasibility": 0.8,
                "n_runs": 0,
                "command": P1_FOLLOWUP,
            },
        ]
        choice_name = "p1-primary"
        priority_note = "P2 stays closed until P1 is approved and reported."
    else:
        phase = "p2"
        candidates = [
            {
                "name": "p2-exploratory",
                "priority": "p2",
                "description": (
                    "Exploratory only after the confirmatory batch is reported: "
                    "one dev seed, not the held-out three-group batch."
                ),
                "expected_learning": 0.45,
                "feasibility": 0.8,
                "n_runs": 4,
                "command": P2_COMMAND,
            },
            {
                "name": "p1-more-seeds",
                "priority": "p1",
                "description": (
                    "Size the held-out batch from the pilot spend log if the "
                    "exploratory cell is declined. n_per_group is not a fixed "
                    "integer until that log has a positive actual cost."
                ),
                "expected_learning": 0.55,
                "feasibility": 0.85,
                "n_runs": 0,
                "command": P1_FOLLOWUP,
            },
        ]
        choice_name = "p2-exploratory"
        priority_note = "P1 has a report, so an exploratory cell may be priced."

    for candidate in candidates:
        candidate["estimated_usd"] = round(candidate["n_runs"] * usd_per_run, 4)
        cost = max(candidate["estimated_usd"], 0.01)
        candidate["score"] = round(
            candidate["expected_learning"] * candidate["feasibility"] / cost,
            4,
        )
        if budget_remaining is None:
            candidate["fits_budget"] = True
        else:
            candidate["fits_budget"] = candidate["estimated_usd"] <= budget_remaining + 1e-9

    chosen = next(item for item in candidates if item["name"] == choice_name)
    if not chosen["fits_budget"]:
        priority_note = (
            f"{choice_name} does not fit the remaining budget. "
            "Caps stay where they are; the planner cannot raise them."
        )
    return {
        "candidates": candidates,
        "choice": chosen["name"],
        "phase": phase,
        "choice_reason": (
            f"{chosen['name']} is next "
            f"(priority {chosen['priority']}, score {chosen['score']}: "
            f"learning {chosen['expected_learning']} "
            f"× feasibility {chosen['feasibility']} / cost ${chosen['estimated_usd']}). "
            f"{priority_note}"
        ),
        "budget_remaining_usd": budget_remaining,
        "usd_per_run": usd_per_run,
        "outcome": "omitted_failure_rate",
    }
