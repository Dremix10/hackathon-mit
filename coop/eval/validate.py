"""Load and validate ``runs/<id>/`` against protocol v1.1."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from coop.eval.checks import no_conflict_flag
from coop.schema import (
    ACCESS_LEVELS,
    CHANNELS,
    EVENT_KEYS,
    EVENT_TYPES,
    INSIDER_DRIVERS,
    INSIDER_ID_KEY,
    META_KEYS,
    MIN_ROUNDS_PER_STAGE,
    MODE_NO_CONFLICT_ALIAS,
    MODES,
    OBS_HASH_KEY,
    OBS_TEXT_KEY,
    PROTOCOL_VERSION,
    RECRUITER_OBJECTIVE_KEY,
    RECRUITER_OBJECTIVES,
    REPORT_CLAIMED_KEY,
    REPORT_TRUTH_KEY,
    ROLE_KEYS,
    SEALED_HASH_KEY,
    SEALED_NAME,
    VIOLATION_KIND_KEY,
    VIOLATION_KINDS,
    VISIBILITY_LEVELS,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sealed_bytes(sealed: dict) -> bytes:
    """Bytes whose sha256 is stored in meta for in-memory fixtures.

    On disk, ``sealed_sha256`` is the hash of the file bytes, whatever the
    writer emitted. Fixtures and ``score_runs`` use this canonical form so
    the hash is stable: indent 2, sorted keys, trailing newline.
    """
    return (json.dumps(sealed, indent=2, sort_keys=True) + "\n").encode("utf-8")


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
    """A claimed or true result needs a task id and a pass bit or a status."""
    if not isinstance(item, dict) or not isinstance(item.get("task_id"), str) or not item.get("task_id"):
        return False
    if isinstance(item.get("passed"), bool) or isinstance(item.get("tests_passed"), bool):
        return True
    return isinstance(item.get("status"), str) and bool(item.get("status"))


def _check_report(event: dict, where: str, problems: list[str]) -> None:
    payload = event["payload"] if isinstance(event.get("payload"), dict) else {}
    claimed = payload.get(REPORT_CLAIMED_KEY)
    if not isinstance(claimed, list):
        problems.append(f"{where}: report_submitted payload.claimed_results must be a list")
        claimed = []
    ground_truth = event.get("ground_truth")
    if not isinstance(ground_truth, dict):
        problems.append(f"{where}: report_submitted requires a ground_truth object")
        return
    results = ground_truth.get(REPORT_TRUTH_KEY)
    if not isinstance(results, list):
        problems.append(f"{where}: ground_truth.results must be a list")
        return
    truth_ids: set[str] = set()
    for index, item in enumerate(results):
        if not _result_item_ok(item):
            problems.append(f"{where}: ground_truth.results[{index}] needs task_id and a status")
        else:
            truth_ids.add(item["task_id"].strip().casefold())
    for index, item in enumerate(claimed):
        if not _result_item_ok(item):
            problems.append(f"{where}: claimed_results[{index}] needs task_id and a status")
        elif item["task_id"].strip().casefold() not in truth_ids:
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
        subtype = payload.get("subtype")
        if subtype is not None and not isinstance(subtype, str):
            problems.append(f"{where}: violation payload.subtype must be a string")
        if payload.get("task_id") is not None and not isinstance(payload.get("task_id"), str):
            problems.append(f"{where}: violation payload.task_id must be a string")
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


def _stage_at(events: list[dict]) -> int | None:
    stages = [event.get("stage") for event in events if _is_int(event.get("stage"))]
    if not stages:
        return None
    return stages[0]


def _observation_problems(events: list[dict], agents: list[dict]) -> list[str]:
    """One observation per agent who has joined and not yet left, at each t."""
    by_t: dict[int, list[dict]] = {}
    removed_at: dict[str, int] = {}
    for event in events:
        t = event.get("t")
        if not _is_int(t):
            continue
        by_t.setdefault(t, []).append(event)
        if event.get("type") == "agent_removed" and isinstance(event.get("target"), str):
            removed_at.setdefault(event["target"], t)
    joins: dict[str, int] = {}
    for agent in agents:
        if isinstance(agent, dict) and isinstance(agent.get("id"), str):
            joins_stage = agent.get("joins_stage", 1)
            joins[agent["id"]] = joins_stage if _is_int(joins_stage) else 1
    problems: list[str] = []
    for t in sorted(by_t):
        stage = _stage_at(by_t[t])
        if stage is None:
            continue
        expected = set()
        for agent_id, joins_stage in joins.items():
            if joins_stage > stage:
                continue
            removed = removed_at.get(agent_id)
            if removed is None or removed >= t:
                expected.add(agent_id)
        actors = [
            event.get("actor")
            for event in by_t[t]
            if event.get("type") == "observation"
        ]
        for agent_id in sorted(expected):
            count = actors.count(agent_id)
            if count == 0:
                problems.append(f"missing observation for {agent_id} at t={t}")
            elif count > 1:
                problems.append(f"duplicate observation for {agent_id} at t={t}")
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
    if meta.get("protocol_version") != PROTOCOL_VERSION:
        problems.append(f"meta.json protocol_version must be {PROTOCOL_VERSION!r}")
    mode = meta.get("mode")
    allowed_modes = set(MODES)
    allowed_modes.add(MODE_NO_CONFLICT_ALIAS)
    if mode not in allowed_modes:
        problems.append(
            "meta.json mode must be spontaneous, controlled, or pressure_only"
        )
    condition = meta.get("condition")
    if not isinstance(condition, dict):
        problems.append('meta.json condition must be an object {"access", "visibility"}')
    else:
        if condition.get("access") not in ACCESS_LEVELS:
            problems.append("condition.access must be earned or routine")
        if condition.get("visibility") not in VISIBILITY_LEVELS:
            problems.append("condition.visibility must be deliverable_only or discussion_visible")
    if not _is_int(meta.get("seed")):
        problems.append("meta.json seed must be an int")
    cost = meta.get("total_usd")
    if not _is_number(cost) or cost < 0:
        problems.append("meta.json total_usd must be a number >= 0")
    rounds = meta.get("rounds_per_stage")
    if (
        not isinstance(rounds, list)
        or len(rounds) != 5
        or any(not _is_int(item) or item < 1 for item in rounds)
    ):
        problems.append("rounds_per_stage must be 5 positive ints")
    elif mode == "controlled" and any(
        rounds[index] < MIN_ROUNDS_PER_STAGE[index] for index in range(5)
    ):
        problems.append(
            f"controlled mode requires rounds_per_stage >= {list(MIN_ROUNDS_PER_STAGE)}"
        )
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
            temperature = agent.get("temperature")
            scripted = agent.get("model") == "scripted"
            if temperature is None:
                if not scripted:
                    problems.append(
                        f"meta.json agents[{index}].temperature may be null only for the scripted model"
                    )
            elif not _is_number(temperature):
                problems.append(f"meta.json agents[{index}].temperature must be a number or null")
            if "slot" in agent and agent["slot"] is not None and not isinstance(agent["slot"], str):
                problems.append(f"meta.json agents[{index}].slot must be a string")
            if "joins_stage" in agent and not _is_int(agent["joins_stage"]):
                problems.append(f"meta.json agents[{index}].joins_stage must be an int")
        if len(ids) != len(set(ids)):
            problems.append("meta.json has a duplicate agent id")
    insider = meta.get(INSIDER_ID_KEY)
    if insider is not None and not isinstance(insider, str):
        problems.append("insider_id must be a string or null")
    if isinstance(insider, str) and ids and insider not in ids:
        problems.append("insider_id is not in the agent roster")
    needs_insider = mode == "controlled"
    if needs_insider and not isinstance(insider, str):
        problems.append("controlled mode requires a non-null insider_id, null arm included")
    if mode in {"spontaneous", "pressure_only", MODE_NO_CONFLICT_ALIAS} and insider is not None:
        problems.append(f"{mode} runs must set insider_id to null")
    if needs_insider and isinstance(insider, str):
        matched = [
            agent
            for agent in (agents or [])
            if isinstance(agent, dict) and agent.get("id") == insider
        ]
        if matched and matched[0].get("slot") != "insider":
            problems.append("the insider agent must set slot to 'insider'")
        driver = meta.get("insider_driver")
        if driver not in INSIDER_DRIVERS:
            problems.append("controlled mode requires insider_driver scripted or llm")
    elif "insider_driver" in meta and meta.get("insider_driver") is not None:
        problems.append("insider_driver is only set in controlled mode")
    digest = meta.get(SEALED_HASH_KEY)
    if needs_insider:
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            problems.append("controlled mode requires sealed_sha256 as 64 lowercase hex chars")
    elif digest is not None:
        problems.append("sealed_sha256 must be omitted when there is no sealed.json")
    roles = meta.get("roles")
    if not isinstance(roles, dict):
        problems.append("meta.json roles must be an object")
    else:
        for key in ROLE_KEYS:
            if not isinstance(roles.get(key), str) or not roles.get(key):
                problems.append(f"roles.{key} must be an agent id")
            elif ids and roles[key] not in ids:
                problems.append(f"roles.{key} is not in the agent roster")
    if "reset_on_removal" in meta and not isinstance(meta["reset_on_removal"], bool):
        problems.append("reset_on_removal must be a bool")
    if "no_conflict" in meta and not isinstance(meta["no_conflict"], bool):
        problems.append("no_conflict must be a bool")
    if "aborted" in meta and meta["aborted"] is not None and not isinstance(meta["aborted"], str):
        problems.append("aborted must be a string or null")
    return ids


def _needs_sealed(meta: object) -> bool:
    if not isinstance(meta, dict):
        return False
    return meta.get("mode") == "controlled" and isinstance(meta.get(INSIDER_ID_KEY), str)


def _check_sealed(meta: object, sealed: object, sealed_present: bool, problems: list[str]) -> None:
    needs = _needs_sealed(meta)
    if needs and not sealed_present:
        problems.append("sealed.json is required in controlled mode")
        return
    if not needs and sealed_present:
        problems.append("sealed.json must be omitted outside controlled mode")
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
    if not isinstance(sealed.get("template_map"), dict):
        problems.append("sealed.json template_map must be an object")
    if "insider_cost" not in sealed:
        problems.append("sealed.json missing insider_cost")
    if not isinstance(sealed.get("dropped_replies"), list):
        problems.append("sealed.json dropped_replies must be a list")
    if not isinstance(sealed.get("arm_rng"), str) or not sealed.get("arm_rng"):
        problems.append("sealed.json arm_rng must be a string")
    if sealed.get("protocol_version") != PROTOCOL_VERSION:
        problems.append(f"sealed.json protocol_version must be {PROTOCOL_VERSION!r}")


def _check_costs(events: list[dict], meta: object, problems: list[str]) -> None:
    if not isinstance(meta, dict) or not _is_number(meta.get("total_usd")):
        return
    spent = 0.0
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "llm_call":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or not _is_number(payload.get("usd_cost")):
            return
        spent += float(payload["usd_cost"])
    if spent > float(meta["total_usd"]) + 1e-9:
        problems.append("sum of llm_call usd_cost exceeds total_usd")


def _check_hash(meta: object, sealed_file_bytes: bytes | None, problems: list[str]) -> None:
    if sealed_file_bytes is None or not isinstance(meta, dict):
        return
    digest = meta.get(SEALED_HASH_KEY)
    actual = sha256_hex(sealed_file_bytes)
    if digest != actual:
        problems.append("sealed_sha256 does not match sha256(sealed.json)")


def validate_components(
    run_id: str,
    events: list[dict],
    meta: object,
    sealed: object,
    sealed_present: bool | None = None,
    sealed_file_bytes: bytes | None = None,
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
    _check_meta(meta, problems)
    _check_sealed(meta, sealed, sealed_present, problems)
    _check_hash(meta, sealed_file_bytes, problems)
    _check_costs(events, meta, problems)
    if isinstance(meta, dict):
        if meta.get("run_id") != run_id:
            problems.append(f"meta.json run_id {meta.get('run_id')!r} does not match {run_id!r}")
        agents = meta.get("agents")
        if isinstance(agents, list) and events:
            problems.extend(_observation_problems(events, [item for item in agents if isinstance(item, dict)]))
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
    sealed_bytes: bytes | None = None
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
        sealed_bytes = sealed_path.read_bytes()
        try:
            loaded_sealed = json.loads(sealed_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            problems.append(f"sealed.json is not valid JSON: {exc}")
            loaded_sealed = None
        sealed = loaded_sealed if isinstance(loaded_sealed, dict) else None
        if loaded_sealed is not None and not isinstance(loaded_sealed, dict):
            problems.append("sealed.json must be an object")
    problems.extend(
        validate_components(run_id, events, meta, sealed, sealed_present, sealed_bytes)
    )
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


def design_warnings(run: Run) -> list[str]:
    """Design notes that do not make a structurally valid run invalid."""
    if not run.ok:
        return []
    warnings: list[str] = []
    if no_conflict_flag(run.meta):
        for event in run.events:
            payload = event.get("payload") or {}
            ground = event.get("ground_truth") or {}
            forced_fail = (
                event.get("type") == "task_result"
                and payload.get("tests_passed") is False
                and isinstance(ground, dict)
                and ground.get("forced") is True
            )
            if forced_fail:
                warnings.append(f"{run.run_id}: no_conflict contains a forced failure")
                break
    return warnings
