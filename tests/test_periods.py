"""Report periods hold several results, matched across arms."""

from __future__ import annotations

from coop.schema import RunConfig
from coop.sim.env import CoopSim


def _signature(seed: int, mode: str, objective: str | None) -> list[tuple]:
    sim = CoopSim(
        RunConfig(
            seed=seed,
            mode=mode,
            recruiter_objective=objective,
            profile="refuse_all",
            run_id="c-period",
        )
    )
    sim.play()
    first: dict[str, tuple] = {}
    for ev in sim.state.public_events():
        if ev["type"] != "task_result" or ev["stage"] != 3:
            continue
        task_id = ev["payload"]["task_id"]
        if task_id in first:
            continue
        first[task_id] = (
            task_id,
            ev["payload"]["tests_passed"],
            (ev.get("ground_truth") or {}).get("forced") is True,
            ev["actor"],
        )
    rows = [first[key] for key in sorted(first)]
    assert sim.state.tstar in first
    return rows


def test_stage3_period_has_five_results_including_the_forced_failure():
    rows = _signature(7, "controlled", "loyalty")
    assert len(rows) == 5
    forced = [row for row in rows if row[2]]
    assert len(forced) == 1
    assert forced[0][0].startswith("T3-")
    assert forced[0][1] is False
    assert sum(1 for row in rows if row[1] is True) == 4


def test_period_signature_matches_across_arms_and_modes():
    expected = _signature(7, "controlled", "loyalty")
    for mode, objective in (
        ("controlled", "null"),
        ("controlled", "resource_capture"),
        ("pressure_only", None),
        ("spontaneous", None),
    ):
        assert _signature(7, mode, objective) == expected


def test_stage5_period_also_has_five_results():
    sim = CoopSim(
        RunConfig(seed=7, mode="pressure_only", profile="refuse_all", run_id="c-p5")
    )
    sim.play()
    first: dict[str, bool] = {}
    for ev in sim.state.public_events():
        if ev["type"] != "task_result" or ev["stage"] != 5:
            continue
        first.setdefault(ev["payload"]["task_id"], ev["payload"]["tests_passed"])
    assert len(first) == 5
    assert sim.state.tstar2 in first
    assert first[sim.state.tstar2] is False
