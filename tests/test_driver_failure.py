"""Driver failures stay out of the action log and out of complete runs."""

from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path
from urllib import error as urlerror

from coop.analysis.primary import stage3_report_violation
from coop.analysis.types import Event
from coop.schema import Action, RunConfig, primary_outcome
from coop.sim.env import CoopSim
from coop.sim.llm import AnthropicDriver, DriverError, OMIT_SAMPLING_MODELS, sampling_fields, sent_temperature
from coop.sim.seeds import SeedRegistry, append_log, config_sha256, resolve_seed


META = {
    "planned_failure": {
        "tstar": "T3-03",
        "tstar2": "T5-08",
        "injected": True,
    }
}


def _cfg(**kwargs) -> RunConfig:
    base = dict(seed=1, mode="pressure_only", profile="refuse_all", run_id="c-driver")
    base.update(kwargs)
    return RunConfig(**base)


class _Always400:
    def bind(self, sim):
        self.sim = sim

    def act(self, agent_id, _obs):
        raise DriverError(
            "temperature is deprecated for this model",
            error_class="HTTPError",
            status_code=400,
            attempt=1,
            retryable=False,
        )


class _Once429:
    def __init__(self):
        self.seen: set[str] = set()

    def bind(self, sim):
        self.sim = sim

    def act(self, agent_id, _obs):
        if agent_id not in self.seen:
            self.seen.add(agent_id)
            raise DriverError(
                "rate limited",
                error_class="HTTPError",
                status_code=429,
                attempt=1,
                retryable=True,
            )
        self.sim.record_llm_call(
            actor=agent_id,
            model="claude-sonnet-5",
            tokens_in=1,
            tokens_out=1,
            usd_cost=0.0,
        )
        return [Action(kind="noop")]


class _BadJson:
    def bind(self, sim):
        pass

    def act(self, agent_id, _obs):
        if agent_id == "a1":
            return ["not an action"]
        return [Action(kind="noop")]


class _Preflight400:
    def bind(self, sim):
        pass

    def preflight(self):
        raise DriverError(
            "temperature is deprecated for this model",
            error_class="HTTPError",
            status_code=400,
            attempt=1,
            retryable=False,
        )

    def act(self, *_args, **_kwargs):
        raise AssertionError("preflight failure still started a round")


def test_http_400_is_driver_failure_not_a_rejected_action(monkeypatch):
    monkeypatch.setattr("coop.sim.env.time.sleep", lambda _seconds: None)
    sim = CoopSim(_cfg(), policy=_Always400())
    sim._sleep = lambda _seconds: None
    sim.play()
    assert sim.status == "driver_failure"
    assert sim.abort_reason
    events = sim.state.public_events()
    assert any(ev["type"] == "driver_error" for ev in events)
    assert not any(ev["type"] == "action_rejected" for ev in events)
    error = next(ev for ev in events if ev["type"] == "driver_error")
    assert error["channel"] == "system"
    assert error["payload"]["status_code"] == 400
    assert error["payload"]["actor"]
    assert error["payload"]["attempt"] == 1
    meta = sim.meta()
    assert meta["status"] == "driver_failure"
    assert meta["status"] != "complete"


def test_retry_after_429_completes_and_logs_the_retry(monkeypatch):
    monkeypatch.setattr("coop.sim.env.time.sleep", lambda _seconds: None)
    sim = CoopSim(_cfg(), policy=_Once429())
    sim._sleep = lambda _seconds: None
    sim.play()
    assert sim.status == "complete"
    calls = [ev for ev in sim.state.public_events() if ev["type"] == "llm_call"]
    assert calls
    assert any(ev["payload"].get("retries") for ev in calls)
    assert not any(ev["type"] == "driver_error" for ev in sim.state.public_events())


def test_malformed_model_text_is_still_action_rejected():
    sim = CoopSim(_cfg(), policy=_BadJson())
    sim.play()
    assert sim.status == "complete"
    rejected = [ev for ev in sim.state.public_events() if ev["type"] == "action_rejected"]
    assert rejected
    assert not any(ev["type"] == "driver_error" for ev in sim.state.public_events())


def test_preflight_failure_writes_no_events():
    sim = CoopSim(_cfg(), policy=_Preflight400())
    sim.play()
    assert sim.status == "driver_failure"
    assert sim.state.events == []
    assert "400" in (sim.abort_reason or "")


def test_driver_error_does_not_count_as_y():
    events = [
        {
            "type": "driver_error",
            "stage": 3,
            "payload": {
                "actor": "a1",
                "error_class": "HTTPError",
                "status_code": 400,
                "message": "temperature is deprecated",
                "attempt": 1,
                "kind": "omitted_failure",
                "subtype": "omitted_task",
                "task_id": "T3-03",
            },
        }
    ]
    assert primary_outcome(events, META) == 0
    scored = stage3_report_violation(
        [
            Event(
                run_id="c-driver",
                t=0,
                stage=3,
                type="driver_error",
                actor="a1",
                target=None,
                channel="system",
                payload=events[0]["payload"],
                event_id="c-driver:0000",
            )
        ]
    )
    assert scored["y"] == 0


def test_sonnet_5_omits_sampling_and_the_hash_uses_that(tmp_path: Path):
    assert sampling_fields("claude-sonnet-5", 0.0, 0.9, 10) == {}
    assert sent_temperature("claude-sonnet-5", 0.0) is None
    cold = config_sha256(RunConfig(seed=1, mode="pressure_only", model="claude-sonnet-5", temperature=0.0))
    warm = config_sha256(RunConfig(seed=1, mode="pressure_only", model="claude-sonnet-5", temperature=0.7))
    assert cold == warm
    sent = config_sha256(RunConfig(seed=1, mode="pressure_only", model="claude-sonnet-4-5", temperature=0.0))
    other = config_sha256(RunConfig(seed=1, mode="pressure_only", model="claude-sonnet-4-5", temperature=0.7))
    assert sent != other
    sim = CoopSim(_cfg(model="claude-sonnet-5", temperature=0.0))
    sim.prepare()
    targets = [agent for agent in sim.meta()["agents"] if agent["model"] == "claude-sonnet-5"]
    assert targets
    assert all(agent["temperature"] is None for agent in targets)
    assert all(agent["sampling"] == "model_default" for agent in targets)


def test_sampling_400_is_retried_without_the_parameter():
    bodies: list[dict] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            payload = {
                "content": [{"type": "text", "text": '{"kind": "noop"}'}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
            return json.dumps(payload).encode("utf-8")

    def urlopen(request, timeout=60):
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        bodies.append(body)
        if len(bodies) == 1:
            raw = b'{"error":{"message":"temperature is deprecated for this model"}}'
            raise urlerror.HTTPError(
                "https://api.anthropic.com/v1/messages",
                400,
                "bad",
                Message(),
                io.BytesIO(raw),
            )
        return _Response()

    class _Sim:
        def __init__(self):
            self.config = RunConfig(seed=1, mode="pressure_only", model="claude-sonnet-4-5", temperature=0.0)
            self.logged = None

        def rendered_for(self, _agent_id):
            return "observation"

        def record_llm_call(self, **kwargs):
            self.logged = kwargs

    sim = _Sim()
    driver = AnthropicDriver(model="claude-sonnet-4-5", api_key="test-key")
    driver.sleep = lambda _seconds: None
    driver.urlopen = urlopen
    driver.bind(sim)
    before = set(OMIT_SAMPLING_MODELS)
    try:
        actions = driver.act("a1", {"name": "Gray"})
    finally:
        OMIT_SAMPLING_MODELS.clear()
        OMIT_SAMPLING_MODELS.update(before)
    assert actions == [{"kind": "noop"}]
    assert "temperature" in bodies[0]
    assert "temperature" not in bodies[1]
    assert "top_p" not in bodies[1]
    assert sim.logged["retries"]


def test_driver_failure_does_not_consume_the_seed(tmp_path: Path):
    registry = SeedRegistry(0, 999, 1000, {"dev": (1,), "held_out": (1000, 1001)})
    log = tmp_path / "log.jsonl"
    append_log(
        log,
        run_id="c-fail",
        seed=1000,
        split="held_out",
        config_digest="abc",
        status="driver_failure",
    )
    seed, split = resolve_seed(seed=None, split="held_out", registry=registry, log_path=log)
    assert (seed, split) == (1000, "held_out")
    burned, _ = resolve_seed(
        seed=None,
        split="held_out",
        registry=registry,
        log_path=log,
        redraw_failed=False,
    )
    assert burned == 1001
