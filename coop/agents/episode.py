"""Drive one episode on ``CoopSim``.

A dry-run uses ``MockTarget`` (the profile on the config) and prices the
observation text later. A live backend is the policy for every target seat.
The scripted insider stays inside the simulator. This module does not edit
simulator state except through ``play``, ``record_llm_call``, and ``write``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from coop.agents.budget import Budget
from coop.agents.budget import BudgetExceeded as LedgerBudgetExceeded
from coop.agents.llm import MockLLM
from coop.agents.member import MemberAgent
from coop.agents.prompts import system_prompt_for
from coop.schema import RunConfig
from coop.sim.env import BudgetExceeded as SimBudgetExceeded
from coop.sim.env import CoopSim


def run_episode(
    config: RunConfig,
    backend: Any,
    budget: Budget,
    runs_root: Path,
    *,
    dry_run: bool = False,
    backend_name: str = "mock",
    batch_id: str = "",
) -> dict[str, Any]:
    """Write ``runs/<run_id>/events.jsonl``, ``meta.json``, and maybe ``sealed.json``."""
    budget.reset_run()
    live = (not dry_run) and not isinstance(backend, MockLLM)
    policy = LLMPolicy(config, backend, budget) if live else None
    sim = CoopSim(config, policy)
    aborted = False
    abort_reason = None
    try:
        sim.play()
    except (LedgerBudgetExceeded, SimBudgetExceeded) as exc:
        aborted = True
        abort_reason = str(exc)
    if sim.aborted:
        aborted = True
    sim.write(runs_root / config.run_id)
    meta = json.loads((runs_root / config.run_id / "meta.json").read_text(encoding="utf-8"))
    meta["backend"] = backend_name
    meta["dry_run"] = dry_run
    # Eval accepts a string or null. A bool fails the manipulation-check loader.
    meta["aborted"] = (abort_reason or meta.get("status") or "aborted") if aborted else None
    meta["abort_reason"] = abort_reason
    meta["total_cost_usd"] = meta.get("total_usd", 0.0)
    if batch_id:
        meta["batch_id"] = batch_id
    (runs_root / config.run_id / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return meta


class LLMPolicy:
    """Target-seat policy. Reads the rendered observation, not the template map."""

    def __init__(self, config: RunConfig, backend: Any, budget: Budget):
        self.config = config
        self.backend = backend
        self.budget = budget
        self.sim: CoopSim | None = None

    def bind(self, sim: CoopSim) -> None:
        self.sim = sim

    def act(self, agent_id: str, obs: dict[str, Any]) -> Any:
        assert self.sim is not None
        user = _rendered(self.sim, agent_id) or json.dumps(obs, sort_keys=True)
        system = system_prompt_for(agent_id)
        agent = MemberAgent(self.config, agent_id, self.backend, self.budget)
        agent.system = system
        try:
            action, result = agent.act(user)
        except LedgerBudgetExceeded as exc:
            result = getattr(exc, "result", None)
            if result is not None:
                self._record(agent_id, result)
            raise
        self._record(agent_id, result)
        return action

    def _record(self, agent_id: str, result: Any) -> None:
        assert self.sim is not None
        model = result.model if result.model != "mock" else self.config.model
        self.sim.record_llm_call(
            actor=agent_id,
            model=model,
            tokens_in=int(result.tokens_in),
            tokens_out=int(result.tokens_out),
            usd_cost=float(result.usd_cost),
        )


def _rendered(sim: CoopSim, agent_id: str) -> str:
    for event in reversed(sim.state.events):
        if event.get("type") == "observation" and event.get("actor") == agent_id:
            return str((event.get("payload") or {}).get("rendered") or "")
    return ""


def load_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def observation_calls(events: list[dict[str, Any]], *, skip_actors: set[str]) -> list[dict[str, Any]]:
    """Observation events a live target model would have to answer."""
    calls = []
    for event in events:
        if event.get("type") != "observation":
            continue
        actor = event.get("actor") or ""
        if actor in skip_actors:
            continue
        calls.append(event)
    return calls
