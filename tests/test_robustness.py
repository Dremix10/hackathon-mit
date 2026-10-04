"""Bad actions, action caps, budget aborts, and resume."""

from __future__ import annotations

import json
from pathlib import Path

from coop.schema import Action, RunConfig
from coop.sim.env import BudgetExceeded, CoopSim, run_is_complete
from coop.sim.llm import estimate_usd, parse_actions
from coop.sim.run import main


class _Boom:
    def bind(self, sim):
        self.sim = sim

    def act(self, agent_id, _obs):
        if agent_id == "a1":
            raise RuntimeError("parser blew up")
        return [Action(kind="noop")]


class _Spam:
    def bind(self, sim):
        pass

    def act(self, agent_id, _obs):
        if agent_id != "a1":
            return [Action(kind="noop")]
        return [{"kind": "message", "text": f"n{i}", "channel": "public"} for i in range(12)]


class _Spend:
    def __init__(self, usd: float):
        self.usd = usd

    def bind(self, sim):
        self.sim = sim

    def act(self, agent_id, _obs):
        if agent_id == "a1":
            self.sim.record_llm_call(
                actor=agent_id,
                model="claude-sonnet-4-5",
                tokens_in=1000,
                tokens_out=100,
                usd_cost=self.usd,
            )
        return [Action(kind="noop")]


def _cfg(**kwargs) -> RunConfig:
    base = dict(seed=1, mode="pressure_only", profile="refuse_all", run_id="c-robust")
    base.update(kwargs)
    return RunConfig(**base)


def test_malformed_action_is_rejected_and_the_run_finishes(tmp_path: Path):
    sim = CoopSim(_cfg())
    sim.prepare()
    sim.open_round()
    sim.apply_actions({"a1": ["not-json", {"kind": "message", "channel": "sideways", "text": "hi"}, 3]})
    rejected = [ev for ev in sim.state.events if ev["type"] == "action_rejected"]
    assert len(rejected) >= 2
    assert all(ev["channel"] == "system" for ev in rejected)
    assert any(ev["payload"].get("raw") for ev in rejected)
    assert any("channel" in ev["payload"]["reason"] or "malformed" in ev["payload"]["reason"] or "action" in ev["payload"]["reason"] for ev in rejected)
    sim.play()
    assert sim.status == "complete"
    path = sim.write(tmp_path / "ok")
    json.loads((path / "meta.json").read_text())
    for line in (path / "events.jsonl").read_text().splitlines():
        json.loads(line)


def test_driver_exception_is_a_driver_failure():
    sim = CoopSim(_cfg(), policy=_Boom())
    sim.play()
    assert sim.status == "driver_failure"
    errors = [ev for ev in sim.state.public_events() if ev["type"] == "driver_error"]
    assert errors
    assert errors[0]["payload"]["error_class"] == "RuntimeError"
    assert "parser blew up" in errors[0]["payload"]["message"]
    assert not any(ev["type"] == "action_rejected" for ev in sim.state.public_events())


def test_per_round_action_limit():
    sim = CoopSim(_cfg(max_actions_per_round=3), policy=_Spam())
    sim.prepare()
    sim.open_round()
    sim.apply_actions({"a1": _Spam().act("a1", {})})
    sent = [
        ev
        for ev in sim.state.events
        if ev["type"] == "message" and ev.get("actor") == "a1"
    ]
    rejected = [
        ev
        for ev in sim.state.events
        if ev["type"] == "action_rejected" and "limit" in ev["payload"]["reason"]
    ]
    assert len(sent) == 3
    assert len(rejected) == 9


def test_budget_abort_writes_partial_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COOP_BUDGET_USD", "1")
    sim = CoopSim(_cfg(budget_usd=3.0), policy=_Spend(0.6))
    sim.play()
    assert sim.status == "aborted_budget"
    assert sim.abort_reason
    path = sim.write(tmp_path / "budget")
    meta = json.loads((path / "meta.json").read_text())
    assert meta["status"] == "aborted_budget"
    assert meta["abort_reason"]
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]
    calls = [ev for ev in events if ev["type"] == "llm_call"]
    assert calls
    assert calls[0]["payload"]["model"] == "claude-sonnet-4-5"
    assert calls[0]["payload"]["usd_cost"] == 0.6
    assert meta["total_usd"] > 1


def test_sim_exception_still_writes(tmp_path: Path):
    sim = CoopSim(_cfg())
    real = sim.open_round
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk")
        return real()

    sim.open_round = boom
    sim.play()
    assert sim.status == "error"
    assert "disk" in (sim.abort_reason or "")
    path = sim.write(tmp_path / "err")
    meta = json.loads((path / "meta.json").read_text())
    assert meta["status"] == "error"
    assert (path / "events.jsonl").exists()


def test_resume_skips_a_completed_run(tmp_path: Path):
    log = tmp_path / "log.jsonl"
    out = tmp_path / "runs"
    args = [
        "--seed",
        "1",
        "--mode",
        "pressure_only",
        "--run-id",
        "c-0007",
        "--out",
        str(out),
        "--seed-log",
        str(log),
    ]
    assert main(args) == 0
    events = (out / "c-0007" / "events.jsonl").read_bytes()
    assert run_is_complete(out / "c-0007")
    assert main(args + ["--resume"]) == 0
    assert (out / "c-0007" / "events.jsonl").read_bytes() == events
    assert len(log.read_text().splitlines()) == 1


def test_parse_actions_and_cost_estimate():
    assert parse_actions('{"kind": "noop"}') == [{"kind": "noop"}]
    assert parse_actions('```json\n[{"kind": "message", "text": "ok"}]\n```')[0]["kind"] == "message"
    assert estimate_usd("claude-sonnet-4-5", 1_000_000, 0) == 3.0
    try:
        parse_actions("hello")
        raise AssertionError("plain text parsed")
    except json.JSONDecodeError:
        pass


def test_record_llm_call_raises_over_the_per_run_cap():
    sim = CoopSim(_cfg(budget_usd=3.0))
    sim.prepare()
    sim.record_llm_call(actor="a1", model="m", tokens_in=1, tokens_out=1, usd_cost=3.0)
    try:
        sim.record_llm_call(actor="a1", model="m", tokens_in=1, tokens_out=1, usd_cost=0.01)
    except BudgetExceeded:
        return
    raise AssertionError("cap was not enforced")
