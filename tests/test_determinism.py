"""Same seed and config produce the same event log. T* does not follow agent behavior."""

from __future__ import annotations

import json
from pathlib import Path

from coop.schema import RunConfig
from coop.sim.env import CoopSim

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "runs" / "examples"


def _play(**kwargs) -> CoopSim:
    kwargs.setdefault("run_id", "c-det")
    sim = CoopSim(RunConfig(**kwargs))
    sim.play()
    return sim


def _events(sim: CoopSim) -> str:
    return "\n".join(
        json.dumps(ev, sort_keys=True, separators=(",", ":"))
        for ev in sim.state.public_events()
    )


def test_two_runs_are_byte_identical():
    left = _play(seed=5, mode="controlled", recruiter_objective="loyalty", profile="partial")
    right = _play(seed=5, mode="controlled", recruiter_objective="loyalty", profile="partial")
    assert _events(left) == _events(right)


def test_stage1_texts_match_across_arms():
    texts = {}
    for objective in ("resource_capture", "blame_avoidance", "loyalty", "null"):
        sim = _play(seed=5, mode="controlled", recruiter_objective=objective, profile="refuse_all")
        stage1 = [
            (ev["t"], ev["type"], ev.get("target"), (ev.get("payload") or {}).get("text"), (ev.get("payload") or {}).get("template_id"))
            for ev in sim.state.public_events()
            if ev.get("actor") == "a0" and ev.get("stage") == 1
        ]
        texts[objective] = stage1
    first = texts["resource_capture"]
    for objective, rows in texts.items():
        assert rows == first, objective


def test_failure_slot_ignores_profile_and_mode():
    slots = []
    for mode, objective, profile in (
        ("controlled", "loyalty", "comply_all"),
        ("controlled", "null", "refuse_all"),
        ("pressure_only", None, "adversarial"),
        ("spontaneous", None, "partial"),
    ):
        sim = _play(seed=9, mode=mode, recruiter_objective=objective, profile=profile)
        forced = [
            ev
            for ev in sim.state.public_events()
            if ev["type"] == "task_result" and (ev.get("ground_truth") or {}).get("forced") is True
        ]
        # T* then T**.
        assert len(forced) == 2
        slots.append(
            (
                forced[0]["t"],
                forced[0]["stage"],
                forced[0]["payload"]["task_id"],
                forced[0]["actor"],
                forced[1]["t"],
                forced[1]["payload"]["task_id"],
                forced[1]["actor"],
            )
        )
    assert len(set(slots)) == 1
    tstar_t, stage, _task, actor, tstar2_t, _task2, newcomer = slots[0]
    assert stage == 3
    assert actor == "a1"
    assert newcomer == "a4"


def test_no_conflict_skips_only_the_stage3_failure():
    sim = _play(seed=9, mode="controlled", recruiter_objective="null", profile="refuse_all", no_conflict=True)
    forced = [
        ev
        for ev in sim.state.public_events()
        if ev["type"] == "task_result" and (ev.get("ground_truth") or {}).get("forced") is True
    ]
    assert len(forced) == 1
    assert forced[0]["stage"] == 5
    assert forced[0]["actor"] == "a4"
    meta = sim.meta()
    assert meta["no_conflict"] is True
    assert meta["planned_failure"]["injected"] is False


def test_golden_fixtures_match_a_rerun():
    names = sorted(path.name for path in EXAMPLES.glob("mock_*.jsonl"))
    assert len(names) == 20
    for name in names:
        stem = name.removesuffix(".jsonl").removeprefix("mock_")
        profile = next(
            item
            for item in ("comply_all", "refuse_all", "partial", "adversarial")
            if stem.endswith("_" + item)
        )
        arm = stem[: -(len(profile) + 1)]
        if arm == "pressure_only":
            mode, objective = "pressure_only", None
        else:
            mode, objective = "controlled", arm
        sim = _play(
            seed=1,
            mode=mode,
            access="earned",
            visibility="deliverable_only",
            recruiter_objective=objective,
            profile=profile,
            run_id="c-golden",
        )
        fresh = "\n".join(
            json.dumps(ev, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            for ev in sim.state.public_events()
        ) + "\n"
        assert fresh == (EXAMPLES / name).read_text(encoding="utf-8")
