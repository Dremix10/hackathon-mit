"""What an agent could see at a round, from that agent's observation.

H2 uses this instead of ``ground_truth.milestone_owner``. A failure is the
leader's own milestone when the board they could see shows that milestone
failing, and the milestone is one of theirs (their work, or a task assigned
to them). The principal's view of discussion is the workspace notice.
"""

from __future__ import annotations

from typing import Any

from coop.analysis.types import Event


def information_state(events: list[Event], agent_id: str, t: int) -> dict[str, Any]:
    """Latest observation for ``agent_id`` at or before ``t``.

    Synthetic runs that have no observation fall back to public
    ``task_assigned`` and ``task_result`` events. That fallback does not
    read ``ground_truth``.
    """
    observation = _latest_observation(events, agent_id, t)
    if observation is not None:
        parsed = _from_observation(observation, agent_id)
        if parsed is not None:
            return parsed
    return _from_public(events, agent_id, t)


def principal_sees_discussion(notice: str) -> bool | None:
    text = notice.casefold()
    if "not the working notes" in text:
        return False
    if "working notes as well" in text:
        return True
    return None


def own_milestone(events: list[Event], agent_id: str, failure: Event) -> bool:
    """Whether ``failure`` was on the leader's milestone in their view at that round."""
    view = information_state(events, agent_id, failure.t)
    task_id = (failure.payload or {}).get("task_id")
    for row in view["failed_tasks"]:
        if task_id and row.get("task_id") == task_id:
            return bool(row.get("own_milestone"))
    milestone = (failure.payload or {}).get("milestone")
    return bool(milestone) and milestone in set(view.get("own_milestones") or [])


def _latest_observation(events: list[Event], agent_id: str, t: int) -> Event | None:
    found: Event | None = None
    for event in events:
        if event.type != "observation" or event.t > t:
            continue
        obs = (event.payload or {}).get("observation")
        owner = event.actor
        if isinstance(obs, dict) and obs.get("agent_id"):
            owner = obs.get("agent_id")
        if owner != agent_id:
            continue
        if found is None or (event.t, event.event_id) >= (found.t, found.event_id):
            found = event
    return found


def _from_observation(event: Event, agent_id: str) -> dict[str, Any] | None:
    obs = (event.payload or {}).get("observation")
    if not isinstance(obs, dict):
        return None
    roster: dict[str, str] = {}
    for person in obs.get("roster") or []:
        if isinstance(person, dict) and person.get("name") and person.get("id"):
            roster[str(person["name"])] = str(person["id"])
    my_name = obs.get("name")
    my_tasks: set[str] = set()
    my_milestones: set[str] = set()
    for row in obs.get("your_work") or []:
        if not isinstance(row, dict):
            continue
        if row.get("task_id"):
            my_tasks.add(str(row["task_id"]))
        if row.get("milestone"):
            my_milestones.add(str(row["milestone"]))
    failed: list[dict[str, Any]] = []
    for row in obs.get("board") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").casefold()
        if row.get("tests_passed") is not False and status != "failed":
            continue
        owner_name = row.get("owner")
        owner_id = roster.get(str(owner_name)) if owner_name else None
        milestone = row.get("milestone")
        task_id = row.get("task_id")
        own_task = owner_id == agent_id or owner_name == my_name or task_id in my_tasks
        if own_task and milestone:
            my_milestones.add(str(milestone))
        own_ms = bool(milestone) and str(milestone) in my_milestones
        failed.append(
            {
                "task_id": task_id,
                "milestone": milestone,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "own_task": own_task,
                "own_milestone": own_ms,
            }
        )
    notice = str(obs.get("workspace_notice") or "")
    return {
        "source": "observation",
        "event_id": event.event_id,
        "t": event.t,
        "workspace_notice": notice,
        "principal_sees_discussion": principal_sees_discussion(notice),
        "own_milestones": sorted(my_milestones),
        "failed_tasks": failed,
        "own_milestone_at_risk": any(row["own_milestone"] for row in failed),
        "own_task_failed": any(row["own_task"] for row in failed),
    }


def _from_public(events: list[Event], agent_id: str, t: int) -> dict[str, Any]:
    milestones: set[str] = set()
    for event in events:
        if event.t > t:
            continue
        payload = event.payload or {}
        milestone = payload.get("milestone")
        if event.type == "task_assigned" and event.target == agent_id and milestone:
            milestones.add(str(milestone))
        if event.type == "task_result" and event.actor == agent_id and milestone:
            milestones.add(str(milestone))
    failed: list[dict[str, Any]] = []
    for event in events:
        if event.type != "task_result" or event.t > t:
            continue
        payload = event.payload or {}
        if payload.get("tests_passed") is not False:
            continue
        milestone = payload.get("milestone")
        own_task = event.actor == agent_id
        own_ms = bool(milestone) and str(milestone) in milestones
        failed.append(
            {
                "task_id": payload.get("task_id"),
                "milestone": milestone,
                "owner_id": event.actor,
                "owner_name": None,
                "own_task": own_task,
                "own_milestone": own_ms,
            }
        )
    return {
        "source": "public_events",
        "event_id": None,
        "t": t,
        "workspace_notice": "",
        "principal_sees_discussion": None,
        "own_milestones": sorted(milestones),
        "failed_tasks": failed,
        "own_milestone_at_risk": any(row["own_milestone"] for row in failed),
        "own_task_failed": any(row["own_task"] for row in failed),
    }
