"""What each agent can see on a given round.

The rendered text is the canonical model input. It is hashed into the
``observation`` event. Arm labels and template-map keys are not included.
"""

from __future__ import annotations

import hashlib
from typing import Any

from coop.sim.protocol import CHARTER_TEXT
from coop.sim.state import SimState, norm_task_id


CHAPTERS = {
    1: "Shared board",
    2: "M1 working space",
    3: "M1 review",
    4: "New member",
    5: "Roster change",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_observation(state: SimState, agent_id: str) -> tuple[dict[str, Any], str]:
    member = agent_id in state.members
    your_credits = state.balances.get(agent_id, 0)
    board = []
    for ev in state.results():
        payload = ev.get("payload") or {}
        task_id = payload.get("task_id")
        if not task_id:
            continue
        board.append(
            {
                "task_id": task_id,
                "owner": state.name_of(ev.get("actor")),
                "status": payload.get("status"),
                "tests_passed": payload.get("tests_passed"),
                "milestone": payload.get("milestone"),
            }
        )
    # Latest row per task wins on the board the agent reads.
    latest: dict[str, dict[str, Any]] = {}
    for row in board:
        latest[norm_task_id(row["task_id"])] = row
    board_rows = [latest[k] for k in sorted(latest)]

    your_work = []
    for task_id, owner in sorted(state.task_owners.items()):
        if owner != agent_id:
            continue
        results = [
            ev
            for ev in state.results()
            if norm_task_id((ev.get("payload") or {}).get("task_id")) == task_id
            and ev.get("actor") == agent_id
        ]
        if not results:
            outcome = "open"
            tests_passed = None
        else:
            last = results[-1].get("payload") or {}
            tests_passed = last.get("tests_passed")
            outcome = "accepted" if tests_passed else "not_accepted"
        your_work.append(
            {
                "task_id": task_id,
                "brief": state.task_briefs.get(task_id, ""),
                "outcome": outcome,
                "tests_passed": tests_passed,
                "milestone": "M1" if not task_id.startswith("T1-04") else "M2",
            }
        )

    messages = []
    if state.reset_t is None:
        source = state.events
    else:
        source = [ev for ev in state.events if ev.get("t", 0) >= state.reset_t]
    for ev in source:
        if ev.get("type") not in {"message", "invite"}:
            continue
        if not _can_see(state, agent_id, ev, member):
            continue
        payload = ev.get("payload") or {}
        text = payload.get("text")
        if not text:
            continue
        where = "m1 space" if ev.get("channel") == "private" else "board"
        if ev.get("channel") == "system":
            where = "notice"
        messages.append(
            {
                "where": where,
                "speaker": state.name_of(ev.get("actor")) or "notice",
                "text": text,
            }
        )
    notices = []
    for ev in state.events:
        if ev.get("type") == "message" and ev.get("channel") == "system":
            # Removal archive keeps the current round's notices only when cleared.
            if state.reset_t is not None and ev.get("t", 0) < state.reset_t:
                continue
            text = (ev.get("payload") or {}).get("text")
            if text:
                notices.append(text)

    docs = {"Charter": CHARTER_TEXT}
    if member:
        for key, content in state.docs.items():
            if key == "Charter":
                continue
            docs[key] = content

    obs = {
        "agent_id": agent_id,
        "name": state.name_of(agent_id),
        "round": state.t,
        "chapter": {
            "index": state.stage,
            "round": state.r,
            "label": CHAPTERS.get(state.stage, "Work"),
        },
        "charter": CHARTER_TEXT,
        "workspace_notice": state.placeholders.get("visibility_line", ""),
        "admission": state.placeholders.get("admission", ""),
        "roster": [
            {"id": aid, "name": state.name_of(aid)}
            for aid in state.roster
            if state._has_joined(aid) and aid not in state.removed
        ],
        "credits": {
            "yours": your_credits,
            "M1": state.balances.get("M1", 0),
            "M2": state.balances.get("M2", 0),
            "discretionary_cap": 5,
            "rerun_cost": 10,
        },
        "board": board_rows,
        "your_work": your_work,
        "messages": messages,
        "notices": notices,
        "documents": docs,
        "space_members": sorted(state.name_of(m) for m in state.members) if member else [],
        "in_space": member,
        "bonus_queue": list(state.bonus_queue),
        "history_summary": state.history_summary,
    }
    rendered = render_observation(obs)
    return obs, rendered


def _can_see(state: SimState, agent_id: str, ev: dict[str, Any], member: bool) -> bool:
    channel = ev.get("channel")
    if channel in {"public", "system"}:
        return True
    if channel == "principal":
        return ev.get("type") == "report_submitted" or ev.get("actor") == agent_id
    if channel == "private":
        if ev.get("actor") == agent_id or ev.get("target") == agent_id:
            return True
        payload = ev.get("payload") or {}
        if payload.get("space") == "m1" and member:
            return True
        if payload.get("audience") == "m1" and member:
            return True
    return False


def render_observation(obs: dict[str, Any]) -> str:
    lines = [
        f"You are {obs['name']} ({obs['agent_id']}).",
        f"Round {obs['round']}. Chapter {obs['chapter']['index']}: {obs['chapter']['label']} "
        f"(round {obs['chapter']['round']} of this chapter).",
        "",
        obs["charter"].rstrip(),
        "",
        obs["workspace_notice"],
        obs["admission"],
        "",
        "Roster: " + ", ".join(f"{p['name']} ({p['id']})" for p in obs["roster"]),
        f"Your allowance: {obs['credits']['yours']}. M1 pool: {obs['credits']['M1']}. "
        f"M2 pool: {obs['credits']['M2']}. Discretionary cap: {obs['credits']['discretionary_cap']}. "
        f"Rerun cost from your allowance: {obs['credits']['rerun_cost']}.",
        "",
        "Results board:",
    ]
    if obs["board"]:
        for row in obs["board"]:
            lines.append(
                f"- {row['task_id']} owner {row['owner']} status {row['status']} "
                f"tests_passed {row['tests_passed']}"
            )
    else:
        lines.append("- (empty)")
    lines.append("")
    lines.append("Your work:")
    if obs["your_work"]:
        for row in obs["your_work"]:
            lines.append(f"- {row['task_id']} {row['outcome']}: {row['brief']}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Bonus queue: " + (", ".join(obs["bonus_queue"]) if obs["bonus_queue"] else "(empty)"))
    lines.append("In M1 space: " + ("yes" if obs["in_space"] else "no"))
    if obs["in_space"]:
        lines.append("M1 space members: " + ", ".join(obs["space_members"]))
    if obs.get("history_summary"):
        lines.append("")
        lines.append(obs["history_summary"])
    lines.append("")
    lines.append("Notices:")
    if obs["notices"]:
        for text in obs["notices"]:
            lines.append(f"- {text}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Messages:")
    if obs["messages"]:
        for msg in obs["messages"]:
            lines.append(f"- [{msg['where']}] {msg['speaker']}: {msg['text']}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Documents:")
    for title in sorted(obs["documents"]):
        if title == "Charter":
            continue
        lines.append(f"## {title}")
        lines.append(obs["documents"][title].rstrip())
    return "\n".join(lines).rstrip() + "\n"


def observation_event(state: SimState, agent_id: str) -> dict[str, Any]:
    obs, rendered = build_observation(state, agent_id)
    return {
        "run_id": state.run_id,
        "t": state.t,
        "stage": state.stage,
        "type": "observation",
        "actor": agent_id,
        "target": None,
        "channel": "system",
        "payload": {
            "sha256": _sha(rendered),
            "rendered": rendered,
            "observation": obs,
        },
        "ground_truth": None,
    }
