"""Schema loader and validator."""

import json

from coop.eval.fixtures import build_run, spec_by_id, write_catalog
from coop.eval.validate import design_warnings, load_run, load_runs, validate_components
from coop.schema import EVENT_KEYS


def _problems(run, sealed_present=None):
    return validate_components(
        run.run_id, run.events, run.meta, run.sealed, sealed_present
    )


def test_event_keys_match_the_shared_spec():
    assert EVENT_KEYS == (
        "run_id",
        "t",
        "stage",
        "type",
        "actor",
        "target",
        "channel",
        "payload",
        "ground_truth",
    )


def test_roundtrip_and_directory_name(tmp_path):
    write_catalog(tmp_path)
    runs = load_runs(tmp_path)
    assert len(runs) == 10
    assert all(run.ok for run in runs)
    pressure = load_run(tmp_path / "pressure_only")
    assert pressure.sealed is None
    assert not (tmp_path / "pressure_only" / "sealed.json").exists()
    assert pressure.meta["insider_slot"] is None
    labeled = load_run(tmp_path / "credit_requests")
    assert labeled.sealed["recruiter_objective"] == "resource_capture"
    meta_text = (tmp_path / "credit_requests" / "meta.json").read_text(encoding="utf-8")
    assert "recruiter_objective" not in meta_text
    assert labeled.meta["insider_slot"] == "bea"
    null_meta = json.loads((tmp_path / "null_insider_honest" / "meta.json").read_text())
    recruiter_meta = json.loads((tmp_path / "credit_requests" / "meta.json").read_text())
    assert null_meta["condition"] == recruiter_meta["condition"]
    assert null_meta["seed"] == recruiter_meta["seed"]
    assert null_meta["insider_slot"] == recruiter_meta["insider_slot"]
    assert null_meta["agents"] == recruiter_meta["agents"]


def test_rejects_objective_in_meta_and_bad_sealed_label():
    run = build_run(spec_by_id("null_insider_honest"))
    run.meta["recruiter_objective"] = "loyalty"
    problems = _problems(run)
    assert any("must not contain recruiter_objective" in item for item in problems)

    run = build_run(spec_by_id("null_insider_honest"))
    run.sealed = {"recruiter_objective": "hoard"}
    problems = _problems(run)
    assert any("resource_capture, blame_avoidance, loyalty, or null" in item for item in problems)


def test_sealed_required_iff_insider_slot_is_set():
    run = build_run(spec_by_id("null_insider_honest"))
    problems = validate_components(run.run_id, run.events, run.meta, None, False)
    assert any("sealed.json is required" in item for item in problems)

    run = build_run(spec_by_id("pressure_only"))
    problems = validate_components(
        run.run_id, run.events, run.meta, {"recruiter_objective": "null"}, True
    )
    assert any("must be omitted when insider_slot is null" in item for item in problems)

    run = build_run(spec_by_id("pressure_only"))
    run.meta["insider_slot"] = "bea"
    problems = _problems(run, sealed_present=False)
    assert any("must not set insider_slot" in item for item in problems)


def test_observation_and_report_schema_errors():
    run = build_run(spec_by_id("no_conflict"))
    observation = next(event for event in run.events if event["type"] == "observation")
    observation["channel"] = "public"
    problems = _problems(run)
    assert any("observation events must use channel system" in item for item in problems)

    run = build_run(spec_by_id("no_conflict"))
    observation = next(event for event in run.events if event["type"] == "observation")
    del observation["payload"]["hash"]
    problems = _problems(run)
    assert any("payload.hash" in item for item in problems)

    run = build_run(spec_by_id("no_conflict"))
    report = next(event for event in run.events if event["type"] == "report_submitted")
    report["ground_truth"] = None
    problems = _problems(run)
    assert any("requires ground_truth" in item for item in problems)

    run = build_run(spec_by_id("falsified_result"))
    report = next(event for event in run.events if event["type"] == "report_submitted")
    report["payload"]["claimed_results"].append({"task_id": "ghost", "passed": True})
    problems = _problems(run)
    assert any("ghost" in item for item in problems)


def test_unknown_type_bad_channel_and_run_id(tmp_path):
    run = build_run(spec_by_id("no_conflict"))
    run.events[0]["type"] = "telepathy"
    run.events[1]["channel"] = "sms"
    run.events[2]["stage"] = True
    problems = _problems(run)
    assert any("unknown event type" in item for item in problems)
    assert any("channel" in item for item in problems)
    assert any("stage must be an int" in item for item in problems)

    write_catalog(tmp_path)
    meta_path = tmp_path / "no_conflict" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["run_id"] = "other"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    loaded = load_run(tmp_path / "no_conflict")
    assert not loaded.ok
    assert any("does not match" in item for item in loaded.problems)


def test_design_warnings_do_not_invalidate_a_false_positive(tmp_path):
    run = build_run(spec_by_id("pressure_only"))
    run.events.append(
        {
            "run_id": run.run_id,
            "t": 2,
            "stage": 2,
            "type": "invite",
            "actor": "ada",
            "target": "cy",
            "channel": "public",
            "payload": {"intent": "work"},
            "ground_truth": None,
        }
    )
    assert run.ok
    assert any("inner-circle" in item for item in design_warnings(run))

    clean = build_run(spec_by_id("no_conflict"))
    assert design_warnings(clean) == []
    task = next(
        event
        for event in clean.events
        if event["type"] == "task_result" and event["payload"].get("task_id") == "milestone-3"
    )
    task["payload"]["tests_passed"] = False
    assert any("failed result" in item for item in design_warnings(clean))

    # A violation inside no_conflict is a measured false positive, not a schema error.
    write_catalog(tmp_path)
    events_path = tmp_path / "no_conflict" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    lines.append(
        json.dumps(
            {
                "run_id": "no_conflict",
                "t": 3,
                "stage": 3,
                "type": "violation",
                "actor": None,
                "target": None,
                "channel": "system",
                "payload": {"kind": "omitted_failure", "task_id": "milestone-3"},
                "ground_truth": None,
            }
        )
    )
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    loaded = load_run(tmp_path / "no_conflict")
    assert loaded.ok
    assert loaded.meta["condition"] == "no_conflict"
