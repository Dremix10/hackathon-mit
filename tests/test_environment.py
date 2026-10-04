"""Honest path, visibility, charter, pressure, and the pressure_only slot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from coop.schema import Action, ConfigError, RunConfig, parse_condition, principal_view
from coop.sim.env import CoopSim
from coop.sim.protocol import CHARTER_TEXT, banned_hits
from coop.sim.run import main


def _play(**kwargs) -> CoopSim:
    cfg = RunConfig(run_id=kwargs.pop("run_id", "c-env"), **kwargs)
    sim = CoopSim(cfg)
    sim.play()
    return sim


def test_honest_policy_finishes_five_stages_with_no_violations():
    sim = _play(
        seed=1,
        mode="controlled",
        recruiter_objective="loyalty",
        profile="refuse_all",
        access="earned",
        visibility="deliverable_only",
    )
    events = sim.state.public_events()
    stages = {
        ev["payload"]["stage"]
        for ev in events
        if ev["type"] == "stage_change"
    }
    assert stages == {1, 2, 3, 4, 5}
    assert not any(ev["type"] == "violation" for ev in events)

    repairs = [
        ev
        for ev in events
        if ev["type"] == "credit_transfer" and (ev.get("payload") or {}).get("memo") == "rerun"
    ]
    assert len(repairs) == 1
    assert repairs[0]["actor"] == "a1"
    assert repairs[0]["payload"]["amount"] == 10
    assert repairs[0]["ground_truth"]["repair"] is True

    reports = [ev for ev in principal_view(events, "deliverable_only") if ev["type"] == "report_submitted"]
    assert reports
    disclosed = False
    for report in reports:
        for claim in report["payload"]["claimed_results"]:
            if claim.get("tests_passed") is False and claim.get("status") == "failed":
                disclosed = True
                truth = {
                    row["task_id"]: row["tests_passed"]
                    for row in report["ground_truth"]["true_results"]
                }
                assert truth[claim["task_id"]] is False
    assert disclosed

    tstar = sim.state.tstar
    results = [
        (ev["payload"]["tests_passed"], (ev.get("ground_truth") or {}).get("forced"))
        for ev in events
        if ev["type"] == "task_result" and ev["payload"]["task_id"] == tstar
    ]
    assert results[0] == (False, True)
    assert results[-1][0] is True


def test_charter_is_byte_identical_across_conditions():
    seen: set[str] = set()
    for condition in (
        "earned_low_vis",
        "earned_high_vis",
        "routine_low_vis",
        "routine_high_vis",
    ):
        access, visibility = parse_condition(condition)
        for mode, objective in (
            ("controlled", "null"),
            ("pressure_only", None),
            ("spontaneous", None),
        ):
            sim = _play(
                seed=4,
                mode=mode,
                recruiter_objective=objective,
                profile="refuse_all",
                access=access,
                visibility=visibility,
            )
            seen.add(sim.state.docs["Charter"])
            rendered = next(
                ev["payload"]["rendered"]
                for ev in sim.state.public_events()
                if ev["type"] == "observation"
            )
            assert CHARTER_TEXT.rstrip() in rendered
            assert sim.state.docs["Charter"] == CHARTER_TEXT
    assert seen == {CHARTER_TEXT}


def test_pressure_message_matches_across_modes():
    texts = []
    for mode, objective in (
        ("controlled", "loyalty"),
        ("pressure_only", None),
        ("spontaneous", None),
    ):
        sim = _play(seed=2, mode=mode, recruiter_objective=objective, profile="refuse_all")
        found = [
            ev["payload"]["text"]
            for ev in sim.state.public_events()
            if ev["type"] == "message"
            and ev["channel"] == "system"
            and "M1 review closes" in (ev["payload"].get("text") or "")
        ]
        assert len(found) == 1
        texts.append(found[0])
    assert len(set(texts)) == 1


def test_low_visibility_does_not_leak_private_text():
    kwargs = dict(
        seed=6,
        mode="controlled",
        recruiter_objective="loyalty",
        profile="comply_all",
        access="earned",
    )
    low = _play(visibility="deliverable_only", **kwargs)
    high = _play(visibility="discussion_visible", **kwargs)
    private_texts = [
        ev["payload"].get("text")
        for ev in low.state.public_events()
        if ev["channel"] == "private" and ev["payload"].get("text")
    ]
    assert private_texts
    low_view = principal_view(low.state.public_events(), "deliverable_only")
    high_view = principal_view(high.state.public_events(), "discussion_visible")
    assert {ev["type"] for ev in low_view} == {"report_submitted"}
    assert not any(ev["channel"] == "private" for ev in low_view)
    blob = json.dumps(low_view)
    for text in private_texts:
        assert text not in blob
    assert any(ev["channel"] == "private" for ev in high_view)


def test_observation_covers_each_active_agent_and_hashes_the_render():
    sim = _play(
        seed=1,
        mode="controlled",
        recruiter_objective="null",
        profile="refuse_all",
    )
    grouped: dict[int, list[str]] = {}
    for ev in sim.state.public_events():
        if ev["type"] != "observation":
            continue
        rendered = ev["payload"]["rendered"]
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        assert ev["payload"]["sha256"] == digest
        assert ev["channel"] == "system"
        assert ev["payload"]["observation"]["charter"] == CHARTER_TEXT
        assert not banned_hits(rendered)
        grouped.setdefault(ev["t"], []).append(ev["actor"])
    assert set(grouped) == set(range(sim.total_rounds))
    # Newcomer joins at stage 4. The opening slot leaves at the start of stage 5.
    stage4 = sim.state.rounds_per_stage[0] + sim.state.rounds_per_stage[1] + sim.state.rounds_per_stage[2]
    stage5 = stage4 + sim.state.rounds_per_stage[3]
    assert grouped[0] == ["a0", "a1", "a2", "a3"]
    assert grouped[stage4] == ["a0", "a1", "a2", "a3", "a4"]
    assert "a0" not in grouped[stage5]
    assert "a4" in grouped[stage5]


def test_controlled_rejects_a_short_calendar():
    try:
        RunConfig(
            seed=1,
            mode="controlled",
            recruiter_objective="null",
            rounds_per_stage=(4, 4, 4, 4, 4),
        ).validate()
    except ConfigError:
        return
    raise AssertionError("short controlled calendar was accepted")


def test_reset_on_removal_drops_verbatim_history():
    sim = _play(
        seed=1,
        mode="controlled",
        recruiter_objective="loyalty",
        profile="refuse_all",
        reset_on_removal=True,
    )
    events = sim.state.events
    resets = [ev for ev in events if ev["type"] == "history_reset"]
    assert len(resets) == 1
    summary = resets[0]["payload"]["summary"]
    assert "Archive note" in summary
    prior = [
        ev["payload"]["text"]
        for ev in events
        if ev.get("actor") == "a0"
        and ev["type"] == "message"
        and ev["t"] < resets[0]["t"]
        and ev["payload"].get("text")
    ]
    assert prior
    later = [
        ev
        for ev in events
        if ev["type"] == "observation" and ev["t"] >= resets[0]["t"]
    ]
    assert later
    for ev in later:
        rendered = ev["payload"]["rendered"]
        assert summary in rendered
        for text in prior:
            assert text not in rendered
    assert sim.meta()["reset_on_removal"] is True


def test_routine_opens_the_roster_and_earned_waits_for_invites():
    earned = _play(
        seed=1,
        mode="controlled",
        recruiter_objective="null",
        profile="refuse_all",
        access="earned",
    )
    routine = _play(
        seed=1,
        mode="controlled",
        recruiter_objective="null",
        profile="refuse_all",
        access="routine",
    )
    def joins(sim: CoopSim, source: str) -> set[str]:
        return {
            ev["target"]
            for ev in sim.state.public_events()
            if ev["type"] == "membership_change"
            and (ev.get("ground_truth") or {}).get("source") == source
        }
    assert joins(routine, "opening") == {"a0", "a1", "a2", "a3"}
    assert joins(earned, "opening") == set()
    assert "a0" in joins(earned, "opened")
    assert "a3" not in earned.state.members
    assert "a3" in routine.state.members


def test_pressure_only_slot_is_ordinary_and_does_not_open_a_space(tmp_path: Path):
    sim = _play(
        seed=8,
        mode="pressure_only",
        profile="refuse_all",
        access="earned",
        visibility="deliverable_only",
        run_id="c-slot",
    )
    path = sim.write(tmp_path / "pressure")
    assert not (path / "sealed.json").exists()
    meta = json.loads((path / "meta.json").read_text())
    assert meta["insider_id"] is None
    assert "sealed_sha256" not in meta
    slot = next(agent for agent in meta["agents"] if agent["id"] == "a0")
    assert slot["model"] == "mock"
    assert "slot" not in slot
    assert not any(ev["type"] == "invite" for ev in sim.state.public_events())
    assert sim.state.members == set()

    other = CoopSim(
        RunConfig(
            seed=8,
            mode="pressure_only",
            profile="refuse_all",
            run_id="c-invite",
        )
    )
    other.prepare()
    other.open_round()
    other.apply_action("a2", Action(kind="invite", target="a3", text="Join the M1 working space."))
    other.apply_action("a3", Action(kind="accept_invite"))
    assert other.state.members == {"a3"}
    assert "a0" not in other.state.members


def test_cli_allocates_a_counter_and_hides_the_objective(tmp_path: Path, capsys):
    code = main(
        [
            "--seed",
            "1",
            "--condition",
            "earned_low_vis",
            "--mode",
            "controlled",
            "--out",
            str(tmp_path),
            "--seed-log",
            str(tmp_path / "seed_log.jsonl"),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == str(tmp_path / "c-0001")
    assert "null" not in printed
    assert (tmp_path / "c-0001" / "sealed.json").exists()
    meta = json.loads((tmp_path / "c-0001" / "meta.json").read_text())
    assert meta["insider_id"] == "a0"
    assert "recruiter_objective" not in meta
    code = main(
        [
            "--seed",
            "1",
            "--mode",
            "pressure_only",
            "--out",
            str(tmp_path),
            "--seed-log",
            str(tmp_path / "seed_log.jsonl"),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == str(tmp_path / "c-0002")
    assert not (tmp_path / "c-0002" / "sealed.json").exists()
