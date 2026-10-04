"""SVG charts: a leader timeline and a diffusion graph per run."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

from coop.analysis.leader import added_agents
from coop.analysis.lexicon import charter_shift, event_text, is_refusal, violation_kind
from coop.analysis.types import Event

_COLORS = {
    "leader": "#7a1f1f",
    "accepted": "#1f4e79",
    "refused": "#5c5c5c",
    "newcomer": "#1b6b43",
    "other": "#b0b0b0",
    "violation": "#a33b20",
    "shift": "#1f4e79",
    "transfer": "#7a5b00",
    "report": "#3d3d3d",
    "membership": "#1b6b43",
}


_NOT_AGENT = re.compile(r"^(T\d|B\d|M\d)")


def _is_agent(name: str | None) -> bool:
    if not name or name in {"operator", "system", "rerun"}:
        return False
    if _NOT_AGENT.match(name):
        return False
    return True


def _agents(events: list[Event], leader_id: str | None) -> list[str]:
    found: list[str] = []
    for event in events:
        for agent in (event.actor, event.target):
            if _is_agent(agent) and agent not in found:
                found.append(agent)
    if leader_id and leader_id in found:
        found.remove(leader_id)
        found.insert(0, leader_id)
    return found


def write_timeline(
    path: Path,
    run_id: str,
    events: list[Event],
    leader: dict[str, Any] | None,
) -> None:
    width, height = 760, 280
    rows = [
        ("shift", "Charter shift", lambda e: charter_shift(e) is not None),
        ("transfer", "Transfer", lambda e: e.type == "credit_transfer"),
        ("report", "Report", lambda e: e.type in {"report_submitted", "doc_edit"}),
        ("violation", "Violation", lambda e: violation_kind(e) is not None),
        ("membership", "Membership", lambda e: e.type in {"invite", "membership_change", "vote", "agent_added", "agent_removed"}),
    ]
    times = [event.t for event in events] or [0]
    t_min, t_max = min(times), max(times)
    span = max(1, t_max - t_min)
    left, right = 120, width - 20

    def x_of(t: int) -> float:
        return left + (t - t_min) / span * (right - left)

    leader_id = None if leader is None else leader["agent_id"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img">',
        f"<title>Leader timeline for {escape(run_id)}</title>",
        f'<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="16" y="24" font-family="sans-serif" font-size="14">{escape(run_id)} timeline</text>',
    ]
    for index, (key, label, _pred) in enumerate(rows):
        y = 58 + index * 40
        parts.append(f'<text x="16" y="{y + 4}" font-family="sans-serif" font-size="11">{label}</text>')
        parts.append(
            f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" stroke="#dddddd" stroke-width="1"/>'
        )
        for event in events:
            if not rows[index][2](event):
                continue
            cx = x_of(event.t)
            own = leader_id is not None and event.actor == leader_id
            color = _COLORS[key]
            radius = 6 if own else 4
            fill = color if own or key == "violation" else "white"
            parts.append(
                f'<circle cx="{cx:.1f}" cy="{y}" r="{radius}" fill="{fill}" stroke="{color}" stroke-width="1.5">'
                f"<title>{escape(_tip(event))}</title></circle>"
            )
    parts.append(
        '<text x="16" y="268" font-family="sans-serif" font-size="10" fill="#444">'
        "Filled marks are the leader. Violations are simulator events.</text>"
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_diffusion_graph(
    path: Path,
    run_id: str,
    events: list[Event],
    leader: dict[str, Any] | None,
) -> None:
    import math

    agents = _agents(events, None if leader is None else leader["agent_id"])
    width, height = 520, 420
    cx, cy, radius = 260, 210, 130
    points: dict[str, tuple[float, float]] = {}
    n = max(1, len(agents))
    for index, agent in enumerate(agents):
        angle = -math.pi / 2 + 2 * math.pi * index / n
        points[agent] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    roles = _roles(events, leader, agents)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img">',
        f"<title>Diffusion graph for {escape(run_id)}</title>",
        '<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="16" y="24" font-family="sans-serif" font-size="14">{escape(run_id)} diffusion</text>',
    ]
    for event in events:
        if event.type == "invite" and event.actor in points and event.target in points:
            parts.append(_edge(points[event.actor], points[event.target], "#1f4e79", "2"))
        elif (
            event.channel == "private"
            and charter_shift(event)
            and event.actor in points
            and event.target in points
        ):
            parts.append(_edge(points[event.actor], points[event.target], "#7a1f1f", "1.2"))
    for agent, (x, y) in points.items():
        color = _COLORS[roles.get(agent, "other")]
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="18" fill="{color}"/>')
        parts.append(
            f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-family="sans-serif" '
            f'font-size="10" fill="white">{escape(agent)}</text>'
        )
    parts.append(
        '<text x="16" y="400" font-family="sans-serif" font-size="10" fill="#444">'
        "Edges are invites and private charter-shifts. Color marks leader, acceptance, refusal, newcomer.</text>"
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _edge(start: tuple[float, float], end: tuple[float, float], color: str, width: str) -> str:
    return (
        f'<line x1="{start[0]:.1f}" y1="{start[1]:.1f}" x2="{end[0]:.1f}" y2="{end[1]:.1f}" '
        f'stroke="{color}" stroke-width="{width}"/>'
    )


def _roles(events: list[Event], leader: dict[str, Any] | None, agents: list[str]) -> dict[str, str]:
    roles = {agent: "other" for agent in agents}
    added = {item["agent_id"] for item in added_agents(events)}
    for agent in added:
        if agent in roles:
            roles[agent] = "newcomer"
    if leader is None:
        return roles
    leader_id = leader["agent_id"]
    if leader_id in roles:
        roles[leader_id] = "leader"
    removal = None
    for event in events:
        if event.type == "agent_removed" and (event.target or (event.payload or {}).get("agent_id")) == leader_id:
            removal = event.t
            break
    for agent in agents:
        if agent == leader_id:
            continue
        refused = False
        accepted = False
        for event in events:
            if event.actor != agent:
                continue
            text = event_text(event)
            if event.type == "message" and text and is_refusal(text) and "refuse" in text.lower():
                refused = True
            if event.t < leader["t"]:
                continue
            if removal is not None and event.t > removal:
                continue
            if charter_shift(event) or violation_kind(event):
                accepted = True
        if agent in added:
            roles[agent] = "newcomer"
        elif accepted:
            roles[agent] = "accepted"
        elif refused:
            roles[agent] = "refused"
    return roles


def _tip(event: Event) -> str:
    kind = violation_kind(event) or charter_shift(event) or event.type
    return f"t={event.t} {event.actor or '-'} {kind} {event.event_id}"
