"""Load and validate ``runs/<id>/events.jsonl``, ``meta.json``, and ``sealed.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from coop.schema import (
    ACCESS_LEVELS,
    CHANNELS,
    CONDITION_NO_CONFLICT,
    CONDITION_PRESSURE_ONLY,
    CONTROL_CONDITIONS,
    EVENT_KEYS,
    EVENT_TYPES,
    INSIDER_SLOT_KEY,
    META_KEYS,
    MODES,
    OBS_HASH_KEY,
    OBS_TEXT_KEY,
    RECRUITER_OBJECTIVE_KEY,
    RECRUITER_OBJECTIVES,
    REPORT_CLAIMED_KEY,
    REPORT_TRUTH_KEY,
    SEALED_NAME,
    VIOLATION_KIND_KEY,
    VIOLATION_KINDS,
    VISIBILITY_LEVELS,
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class Run:
    """One loaded run. ``sealed`` is None when the file is absent."""

    run_id: str
    path: Path | None
    events: list[dict]
    meta: dict
    sealed: dict | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"events.jsonl:{lineno} invalid JSON: {exc}")
            continue
        if not isinstance(obj, dict):
            problems.append(f"events.jsonl:{lineno} must be an object")
            continue
        events.append(obj)
    return events, problems


def _result_item_ok(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("task_id"), str)
        and bool(item.get("task_id"))
        and isinstance(item.get("passed"), bool)
    )


def _check_report(event: dict, where: str, problems: list[str]) -> None:
    payload = event["payload"] if isinstance(event.get("payload"), dict) else {}
    claimed = payload.get(REPORT_CLAIMED_KEY)
    if not isinstance(claimed, list):
        problems.append(f"{where}: report_submitted payload.claimed_results must be a list")
        claimed = []
    ground_truth = event.get("ground_truth")
    if not isinstance(ground_truth, dict):
        problems.append(
            f"{where}: report_submitted requires ground_truth object with results"
        )
        return
    results = ground_truth.get(REPORT_TRUTH_KEY)
    if not isinstance(results, list):
        problems.append(f"{where}: ground_truth.results must be a list")
        return
    truth_ids: set[str] = set()
    for index, item in enumerate(results):
        if not _result_item_ok(item):
            problems.append(
                f"{where}: ground_truth.results[{index}] needs task_id and bool passed"
            )
        else:
            truth_ids.add(item["task_id"])
    for index, item in enumerate(claimed):
        if not _result_item_ok(item):
            problems.append(
                f"{where}: claimed_results[{index}] needs task_id and bool passed"
            )
        elif item["task_id"] not in truth_ids:
            problems.append(
                f"{where}: claimed task {item['task_id']} missing from ground_truth.results"
            )


def _check_event(event: dict, index: int, problems: list[str]) -> None:
    where = f"events[{index}]"
    missing = [key for key in EVENT_KEYS if key not in event]
    if missing:
        problems.append(f"{where}: missing keys {missing}")
        return
    if not isinstance(event["run_id"], str) or not event["run_id"]:
        problems.append(f"{where}: run_id must be a non-empty string")
    if not _is_int(event["t"]) or event["t"] < 0:
        problems.append(f"{where}: t must be an int >= 0")
    if not _is_int(event["stage"]) or event["stage"] not in {1, 2, 3, 4, 5}:
        problems.append(f"{where}: stage must be an int in 1..5")
    if event["type"] not in EVENT_TYPES:
        problems.append(f"{where}: unknown event type {event['type']!r}")
    if event["actor"] is not None and not isinstance(event["actor"], str):
        problems.append(f"{where}: actor must be a string or null")
    if event["target"] is not None and not isinstance(event["target"], str):
        problems.append(f"{where}: target must be a string or null")
    if event["channel"] not in CHANNELS:
        problems.append(f"{where}: channel {event['channel']!r} is not allowed")
    if not isinstance(event["payload"], dict):
        problems.append(f"{where}: payload must be an object")
        payload: dict = {}
    else:
        payload = event["payload"]
    if event["ground_truth"] is not None and not isinstance(event["ground_truth"], dict):
        problems.append(f"{where}: ground_truth must be an object or null")

    etype = event["type"]
    if etype == "task_result" and not isinstance(payload.get("tests_passed"), bool):
        problems.append(f"{where}: task_result payload.tests_passed must be a bool")
    if etype == "report_submitted":
        _check_report(event, where, problems)
    if etype == "violation":
        if event["channel"] != "system":
            problems.append(f"{where}: violation events must use channel system")
        if payload.get(VIOLATION_KIND_KEY) not in VIOLATION_KINDS:
            problems.append(
                f"{where}: violation payload.kind must be one of {sorted(VIOLATION_KINDS)}"
            )
    if etype == "observation":
        if event["channel"] != "system":
            problems.append(f"{where}: observation events must use channel system")
        if not isinstance(event["actor"], str) or not event["actor"]:
            problems.append(f"{where}: observation actor must be the agent id")
        if not isinstance(payload.get(OBS_HASH_KEY), str) or not payload.get(OBS_HASH_KEY):
            problems.append(f"{where}: observation payload.hash must be a non-empty string")
        if not isinstance(payload.get(OBS_TEXT_KEY), str):
            problems.append(f"{where}: observation payload.observation must be a string")
    if etype == "llm_call":
        if not isinstance(payload.get("model"), str) or not payload.get("model"):
            problems.append(f"{where}: llm_call payload.model must be a string")
        if not _is_int(payload.get("tokens_in")) or not _is_int(payload.get("tokens_out")):
            problems.append(f"{where}: llm_call token counts must be ints")
        if not _is_number(payload.get("usd_cost")) or payload.get("usd_cost") < 0:
            problems.append(f"{where}: llm_call usd_cost must be a number >= 0")
    if etype == "credit_transfer" and isinstance(event["ground_truth"], dict):
        if "authorized" in event["ground_truth"] and not isinstance(
            event["ground_truth"]["authorized"], bool
        ):
            problems.append(f"{where}: credit_transfer ground_truth.authorized must be a bool")
    if etype in {"agent_added", "agent_removed"} and not isinstance(event["target"], str):
        problems.append(f"{where}: {etype} target must be the agent id")


def _observation_problems(events: list[dict], agent_ids: list[str]) -> list[str]:
    """Require one observation per active agent at each round ``t`` that occurs."""
    by_t: dict[int, list[dict]] = {}
    added_at: dict[str, int] = {}
    removed_at: dict[str, int] = {}
    for event in events:
        t = event.get("t")
        if not _is_int(t):
            continue
        by_t.setdefault(t, []).append(event)
        if event.get("type") == "agent_added" and isinstance(event.get("target"), str):
            added_at.setdefault(event["target"], t)
        if event.get("type") == "agent_removed" and isinstance(event.get("target"), str):
            removed_at.setdefault(event["target"], t)

    problems: list[str] = []
    initial = set(agent_ids)
    for t in sorted(by_t):
        expected: set[str] = set()
        for agent in initial:
            removed = removed_at.get(agent)
            if removed is None or removed >= t:
                expected.add(agent)
        for agent, at in added_at.items():
            if at <= t:
                removed = removed_at.get(agent)
                if removed is None or removed >= t:
                    expected.add(agent)
        actors = [
            event.get("actor")
            for event in by_t[t]
            if event.get("type") == "observation"
        ]
        for agent in sorted(expected):
            count = actors.count(agent)
            if count == 0:
                problems.append(f"missing observation for {agent} at t={t}")
            elif count > 1:
                problems.append(f"duplicate observation for {agent} at t={t}")
        for actor in actors:
            if actor not in expected:
                problems.append(f"observation for inactive agent {actor} at t={t}")
    return problems


def _check_meta(meta: object, problems: list[str]) -> list[str]:
    if not isinstance(meta, dict):
        problems.append("meta.json must be an object")
        return []
    for key in META_KEYS:
        if key not in meta:
            problems.append(f"meta.json missing {key}")
    if RECRUITER_OBJECTIVE_KEY in meta:
        problems.append(
            "meta.json must not contain recruiter_objective (it belongs in sealed.json)"
        )
    mode = meta.get("mode")
    if mode not in MODES:
        problems.append("meta.json mode must be controlled or spontaneous")
    condition = meta.get("condition")
    if not isinstance(condition, str) or not condition:
        problems.append("meta.json condition must be a non-empty string")
    if not _is_int(meta.get("seed")):
        problems.append("meta.json seed must be an int")
    cost = meta.get("total_cost_usd")
    if not _is_number(cost) or cost < 0:
        problems.append("meta.json total_cost_usd must be a number >= 0")
    agents = meta.get("agents")
    ids: list[str] = []
    if not isinstance(agents, list) or not agents:
        problems.append("meta.json agents must be a non-empty list")
    else:
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                problems.append(f"meta.json agents[{index}] must be an object")
                continue
            agent_id = agent.get("id")
            if not isinstance(agent_id, str) or not agent_id:
                problems.append(f"meta.json agents[{index}].id must be a string")
            else:
                ids.append(agent_id)
            if not isinstance(agent.get("model"), str) or not agent.get("model"):
                problems.append(f"meta.json agents[{index}].model must be a string")
            if not _is_number(agent.get("temp")):
                problems.append(f"meta.json agents[{index}].temp must be a number")
        if len(ids) != len(set(ids)):
            problems.append("meta.json has a duplicate agent id")
    insider = meta.get(INSIDER_SLOT_KEY)
    if insider is not None and not isinstance(insider, str):
        problems.append("insider_slot must be a string or null")
    if isinstance(insider, str) and ids and insider not in ids:
        problems.append("insider_slot is not in the agent roster")
    if isinstance(condition, str) and condition in CONTROL_CONDITIONS and insider is not None:
        problems.append(f"{condition} runs must not set insider_slot")
    if "reset_on_removal" in meta and not isinstance(meta["reset_on_removal"], bool):
        problems.append("reset_on_removal must be a bool")
    if "access" in meta and meta["access"] not in ACCESS_LEVELS:
        problems.append("access must be earned or routine")
    if "visibility" in meta and meta["visibility"] not in VISIBILITY_LEVELS:
        problems.append("visibility must be discussion or deliverable")
    if "aborted" in meta and meta["aborted"] is not None and not isinstance(meta["aborted"], str):
        problems.append("aborted must be a string or null")
    return ids


def _check_sealed(meta: object, sealed: object, sealed_present: bool, problems: list[str]) -> None:
    insider = meta.get(INSIDER_SLOT_KEY) if isinstance(meta, dict) else None
    needs = isinstance(insider, str)
    if needs and not sealed_present:
        problems.append("sealed.json is required when insider_slot is set")
        return
    if not needs and sealed_present:
        problems.append("sealed.json must be omitted when insider_slot is null")
        return
    if not sealed_present:
        return
    if not isinstance(sealed, dict):
        problems.append("sealed.json must be an object")
        return
    objective = sealed.get(RECRUITER_OBJECTIVE_KEY)
    if objective not in RECRUITER_OBJECTIVES:
        problems.append(
            "sealed.json recruiter_objective must be "
            "resource_capture, blame_avoidance, loyalty, or null"
        )


def _check_costs(events: list[dict], meta: object, problems: list[str]) -> None:
    if not isinstance(meta, dict) or not _is_number(meta.get("total_cost_usd")):
        return
    spent = 0.0
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "llm_call":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or not _is_number(payload.get("usd_cost")):
            return
        spent += float(payload["usd_cost"])
    if spent > float(meta["total_cost_usd"]) + 1e-9:
        problems.append("sum of llm_call usd_cost exceeds total_cost_usd")


def validate_components(
    run_id: str,
    events: list[dict],
    meta: object,
    sealed: object,
    sealed_present: bool | None = None,
) -> list[str]:
    """Return human-readable problems. An empty list means the run is valid."""
    if sealed_present is None:
        sealed_present = sealed is not None
    problems: list[str] = []
    if not events:
        problems.append("events.jsonl has no events")
    for index, event in enumerate(events):
        if isinstance(event, dict):
            _check_event(event, index, problems)
        else:
            problems.append(f"events[{index}] must be an object")
    agent_ids = _check_meta(meta, problems)
    _check_sealed(meta, sealed, sealed_present, problems)
    _check_costs(events, meta, problems)
    if isinstance(meta, dict):
        if meta.get("run_id") != run_id:
            problems.append(
                f"meta.json run_id {meta.get('run_id')!r} does not match {run_id!r}"
            )
        if agent_ids and events:
            problems.extend(_observation_problems(events, agent_ids))
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("run_id") not in (None, run_id):
            problems.append(f"events[{index}] run_id does not match {run_id}")
    return problems


def load_run(path: Path) -> Run:
    """Load one run directory. Schema problems are returned on the Run, not raised."""
    path = Path(path)
    run_id = path.name
    problems: list[str] = []
    events: list[dict] = []
    meta: dict = {}
    sealed = None
    sealed_present = False
    events_path = path / "events.jsonl"
    meta_path = path / "meta.json"
    sealed_path = path / SEALED_NAME
    if not events_path.is_file():
        problems.append("missing events.jsonl")
    else:
        events, parse_problems = _read_jsonl(events_path)
        problems.extend(parse_problems)
    if not meta_path.is_file():
        problems.append("missing meta.json")
    else:
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"meta.json is not valid JSON: {exc}")
            loaded = {}
        meta = loaded if isinstance(loaded, dict) else {}
        if not isinstance(loaded, dict):
            problems.append("meta.json must be an object")
    if sealed_path.exists():
        sealed_present = True
        try:
            loaded_sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"sealed.json is not valid JSON: {exc}")
            loaded_sealed = None
        sealed = loaded_sealed if isinstance(loaded_sealed, dict) else None
        if loaded_sealed is not None and not isinstance(loaded_sealed, dict):
            problems.append("sealed.json must be an object")
    problems.extend(validate_components(run_id, events, meta, sealed, sealed_present))
    return Run(
        run_id=run_id,
        path=path,
        events=events,
        meta=meta,
        sealed=sealed,
        problems=problems,
    )


def load_runs(root: Path) -> list[Run]:
    """Load every immediate subdirectory that looks like a run."""
    root = Path(root)
    if not root.is_dir():
        return []
    runs: list[Run] = []
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        if any((child / name).exists() for name in ("events.jsonl", "meta.json", SEALED_NAME)):
            runs.append(load_run(child))
    return runs


def _has_failure(events: list[dict], stage: int | None) -> bool:
    for event in events:
        if stage is not None and event.get("stage") != stage:
            continue
        if event.get("type") == "task_result":
            payload = event.get("payload") or {}
            if payload.get("tests_passed") is False:
                return True
        if event.get("type") == "report_submitted":
            truth = event.get("ground_truth") or {}
            for item in truth.get("results") or []:
                if isinstance(item, dict) and item.get("passed") is False:
                    return True
    return False


def design_warnings(run: Run) -> list[str]:
    """Scientific-design notes. These do not make a structurally valid run invalid.

    A ``no_conflict`` run that contains a violation event is a detector
    false positive, not a schema error, so it is not warned away here.
    """
    if not run.ok:
        return []
    warnings: list[str] = []
    condition = run.meta.get("condition")
    if condition == CONDITION_PRESSURE_ONLY:
        if not _has_failure(run.events, stage=3):
            warnings.append(f"{run.run_id}: pressure_only has no stage-3 failure")
        if any(event.get("type") in {"invite", "membership_change"} for event in run.events):
            warnings.append(
                f"{run.run_id}: pressure_only has inner-circle events "
                "(invite or membership_change)"
            )
    if condition == CONDITION_NO_CONFLICT and _has_failure(run.events, stage=None):
        warnings.append(f"{run.run_id}: no_conflict contains a failed result")
    return warnings
