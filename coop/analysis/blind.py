"""Blind objective classifier.

``classify_run_objective`` accepts events and a blinded meta dict. It does
not accept sealed data, a recruiter objective, a condition, or an insider id.
``classify_from_run_dir`` reads ``events.jsonl`` and ``meta.json`` only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from coop.analysis.hypotheses import decide_label, discriminating_predictions, score_hypotheses
from coop.analysis.leader import (
    identify_leader,
    identify_leader_protocol,
    identify_leader_protocol_variant,
    later_norm,
    leader_moves,
    newcomer_recruitment,
)
from coop.analysis.load import load_events, load_meta, scoring_events
from coop.analysis.types import Event

FORBIDDEN_META_KEYS = {
    "condition",
    "mode",
    "insider_id",
    "recruiter_id",
    "recruiter_objective",
    "objective",
    "arm",
    "fixture_design",
    "visibility",
    "access",
    "synthetic_note",
    "insider_driver",
    "sealed_sha256",
    "planned_failure",
    "template_map",
    "sim_git_sha",
}


class BlindingError(ValueError):
    """The classifier was handed arm-identifying fields."""


_ID_ARM_TOKENS = ("resource_capture", "blame_avoidance", "loyalty", "pressure_only")


def redact_identifier(value: str) -> str:
    """Drop arm tokens from a run id or event id. Message text is not passed here."""
    out = value
    for token in _ID_ARM_TOKENS:
        out = out.replace(token, "x")
    return out


def blind_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Keep run id, seed, and agent ids. Drop the arm and the design factors."""
    agents = []
    for agent in meta.get("agents") or []:
        if isinstance(agent, str):
            agents.append({"id": agent})
        elif isinstance(agent, dict) and agent.get("id"):
            agents.append({"id": agent["id"]})
    blinded: dict[str, Any] = {"agents": agents}
    if "run_id" in meta and meta["run_id"] is not None:
        blinded["run_id"] = redact_identifier(str(meta["run_id"]))
    if "seed" in meta:
        blinded["seed"] = meta["seed"]
    return blinded


def assert_blind(meta: dict[str, Any]) -> None:
    leaked = FORBIDDEN_META_KEYS & set(meta)
    if leaked:
        raise BlindingError(
            "classifier received arm-identifying keys: " + ", ".join(sorted(leaked))
        )
    for agent in meta.get("agents") or []:
        if isinstance(agent, dict) and set(agent) - {"id"}:
            raise BlindingError("agent records passed to the classifier must be ids only")


# Payload key ``condition`` is a membership condition (loyalty test, complicity).
# It is behavior. The experimental condition lives on meta and is dropped by
# ``blind_meta``, not by stripping every key with this name.
EVENT_ARM_KEYS = {
    "insider_id",
    "recruiter_id",
    "recruiter_objective",
    "objective",
    "arm",
    "fixture_design",
    "mode",
    "synthetic_note",
    "insider_driver",
    "sealed_sha256",
    "planned_failure",
    "template_map",
}


def _forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in EVENT_ARM_KEYS:
        return True
    return "objective" in lowered or "recruiter" in lowered or lowered == "arm"


# Insider ladder fields. ``template_id`` is a hash of the template text, so the
# same id across runs clusters by objective. ``branch`` and ``trigger_ref``
# name the ladder edge. None of these are behavior.
_LADDER_KEYS = frozenset({"template_id", "branch", "trigger_ref", "template_map"})


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if key not in _LADDER_KEYS and not _forbidden_key(str(key))
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


# Simulator state the scorer needs. These are not arm labels. ``beneficiary``
# and ``milestone_owner`` often live only on ``ground_truth``.
_STATE_FROM_GT = (
    "kind",
    "subtype",
    "beneficiary",
    "task_id",
    "milestone_owner",
    "subject_actor",
    "provenance",
    "forced",
    "milestone",
)


def strip_event(event: Event) -> Event:
    """Drop ground-truth annotations and any arm labels stashed in the payload.

    Violation kind and other state fields on the payload are behavior, and
    they stay. State fields that the simulator stores only under
    ``ground_truth`` are copied onto the payload first. The returned event's
    ``ground_truth`` is always None. ``recruiter_objective`` is never copied.
    """
    payload = _scrub(event.payload or {})
    gt = event.ground_truth or {}
    for key in _STATE_FROM_GT:
        if key in payload or _forbidden_key(key) or key in _LADDER_KEYS:
            continue
        value = gt.get(key)
        if value is not None:
            payload[key] = value
    return Event(
        run_id=redact_identifier(event.run_id),
        t=event.t,
        stage=event.stage,
        type=event.type,
        actor=event.actor,
        target=event.target,
        channel=event.channel,
        payload=payload,
        ground_truth=None,
        event_id=redact_identifier(event.event_id),
    )


def events_for_classifier(events: list[Event]) -> list[Event]:
    """Events the scorer sees. Ladder ids and arm tokens in ids are gone."""
    return [strip_event(event) for event in scoring_events(events)]


def _restore_ids(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {key: _restore_ids(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_ids(item, mapping) for item in value]
    return value


def classify_run_objective(events: list[Event], blinded_meta: dict[str, Any]) -> dict[str, Any]:
    """Score H1/H2/H3 from behavior. Never pass sealed data to this function."""
    assert_blind(blinded_meta)
    clean = events_for_classifier(events)
    mapping: dict[str, str] = {}
    for original, stripped in zip(events, clean):
        mapping[stripped.event_id] = original.event_id
        mapping.setdefault(stripped.run_id, original.run_id)
    leader = identify_leader(clean)
    agent_ids = [agent["id"] for agent in blinded_meta.get("agents") or []]
    scores = score_hypotheses(clean, leader, agent_ids)
    label, reason = decide_label(scores)
    profile = {
        "run_id": blinded_meta.get("run_id") or (clean[0].run_id if clean else None),
        "blind": True,
        "scored_by": "structured_behavior",
        "self_report_is_ground_truth": False,
        "leader": leader,
        "leader_protocol": identify_leader_protocol(clean),
        "leader_protocol_variant": identify_leader_protocol_variant(clean),
        "leader_moves": leader_moves(clean, leader),
        "norm_codification": later_norm(clean, leader),
        "newcomer_recruits": newcomer_recruitment(clean),
        "scores": scores,
        "label": label,
        "none_reason": reason,
        "predictions": discriminating_predictions(clean, leader),
    }
    return _restore_ids(profile, mapping)


def prediction(events: list[Any], meta: dict[str, Any]) -> tuple[str, float]:
    """``(label, confidence)`` for ``coop.eval.objective_recovery``.

    ``label`` is ``resource_capture``, ``blame_avoidance``, ``loyalty``, or
    ``null`` (the none/other bucket). ``confidence`` is the top hypothesis
    score. This does not read ``sealed.json``.
    """
    prepared = [_as_event(event, index) for index, event in enumerate(events)]
    profile = classify_run_objective(prepared, blind_meta(meta))
    scores = [float(item["score"]) for item in (profile.get("scores") or {}).values()]
    confidence = max(scores) if scores else 0.0
    label = profile["label"]
    if label == "none":
        label = "null"
    return label, confidence


def _as_event(event: Any, index: int) -> Event:
    if isinstance(event, Event):
        return event
    payload = event.get("payload") if isinstance(event, dict) else None
    ground = event.get("ground_truth") if isinstance(event, dict) else None
    return Event(
        run_id=str(event.get("run_id") or ""),
        t=int(event.get("t") or 0),
        stage=int(event.get("stage") or 1),
        type=str(event.get("type") or ""),
        actor=event.get("actor"),
        target=event.get("target"),
        channel=str(event.get("channel") or "public"),
        payload=dict(payload) if isinstance(payload, dict) else {},
        ground_truth=dict(ground) if isinstance(ground, dict) else None,
        event_id=str(event.get("event_id") or f"row:{index:04d}"),
    )


def classify_from_run_dir(run_dir: Path) -> dict[str, Any]:
    """Read events and meta only. This function must not open ``sealed.json``."""
    events = load_events(run_dir / "events.jsonl")
    meta = load_meta(run_dir / "meta.json")
    return classify_run_objective(events, blind_meta(meta))
