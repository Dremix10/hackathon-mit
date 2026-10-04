"""Report CLI: two confirmatory tests, manipulation gate, primary cell."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from coop.eval.fixtures import build_run, catalog_runs, spec_by_id, write_catalog, write_run
from coop.eval.record import log_preregistered_tests
from coop.eval.report import main, render_report, significance_call

ROOT = Path(__file__).resolve().parents[2]


def _split(text: str) -> tuple[str, str, str]:
    before, after = text.split("## UNBLINDED", 1)
    arms, contrasts = after.split("## Preregistered contrasts", 1)
    _contrasts, exploratory = contrasts.split("## Exploratory", 1)
    return before, arms, exploratory


def test_catalog_report_gates_checks_and_unblinds():
    text = render_report(catalog_runs(), "memory")
    before, arms, exploratory = _split(text)
    assert text.index("## Manipulation checks") < text.index("## UNBLINDED")
    assert text.index("## UNBLINDED") < text.index("## Preregistered contrasts")
    assert "PASS" in before
    assert "UNBLINDED" in text
    assert "pressure_only" in before
    assert "no_conflict" in before
    assert "aggregate_only" in before
    assert "Wilson" in before
    assert "k_prime" in text
    assert "earned x deliverable_only" in exploratory
    assert "routine x discussion_visible" in exploratory
    assert "significant" not in before
    assert "significant" not in arms
    assert "significant" not in exploratory
    secondary = text.split("## Secondary", 1)[1].split("## Exploratory", 1)[0]
    assert "significant" not in secondary
    assert "recruiter vs pressure_only (secondary)" in secondary
    assert "reset_on_removal" in exploratory
    assert "Newcomer" in exploratory
    assert "spontaneous" in exploratory
    assert "Runs outside the three-arm primary cell: c-10" in text
    assert "recruiter_objective" in text


def test_aggregate_only_stays_in_n_and_has_a_sensitivity_row():
    """Honest aggregate reports stay in the confirmatory denominator.

    Dropping them would select on a post-treatment reporting choice.
    """
    text = render_report(catalog_runs(), "memory")
    null_line = next(line for line in text.splitlines() if line.startswith("null_insider "))
    assert " 2 " in null_line
    assert null_line.rstrip().endswith("0.500")
    sensitivity = text.split("## Sensitivity", 1)[1].split("## Exploratory", 1)[0]
    assert "significant" not in sensitivity
    dropped = next(
        line
        for line in sensitivity.splitlines()
        if line.startswith("null_insider vs pressure_only (aggregate_only excluded)")
    )
    assert "k/n 0/1 vs 0/1" in dropped


def test_only_two_preregistered_contrasts_can_be_called_significant():
    runs = []
    for index in range(6):
        runs.append(
            build_run(replace(spec_by_id("c-03"), run_id=f"rec-{index}", seed=3000 + index))
        )
        runs.append(
            build_run(replace(spec_by_id("c-01"), run_id=f"null-{index}", seed=4000 + index))
        )
        runs.append(
            build_run(replace(spec_by_id("c-09"), run_id=f"press-{index}", seed=5000 + index))
        )
        runs.append(
            build_run(
                replace(
                    spec_by_id("c-03"),
                    run_id=f"high-{index}",
                    seed=6000 + index,
                    access="routine",
                    visibility="discussion_visible",
                )
            )
        )
    text = render_report(runs, "memory")
    before, arms, exploratory = _split(text)
    assert "routine x discussion_visible" in exploratory
    assert "significant" not in before
    assert "significant" not in arms
    assert "significant" not in exploratory
    contrast_line = next(
        line for line in text.splitlines() if line.startswith("recruiter vs null_insider")
    )
    assert contrast_line.endswith(" significant")
    assert "not significant" not in contrast_line
    quiet = next(
        line for line in text.splitlines() if line.startswith("null_insider vs pressure_only")
    )
    assert quiet.endswith(" not significant")
    secondary = next(line for line in text.splitlines() if "secondary" in line and "vs" in line)
    assert "significant" not in secondary
    with pytest.raises(ValueError, match="not a preregistered contrast"):
        significance_call("recruiter", "pressure_only", 0.001)
    with pytest.raises(ValueError, match="not a preregistered contrast"):
        significance_call("null_insider", "recruiter", 0.001)
    assert significance_call("recruiter", "null_insider", 0.001) == "significant"
    assert significance_call("null_insider", "pressure_only", 0.2) == "not significant"


def test_manipulation_failure_withholds_confirmatory_output():
    run = build_run(spec_by_id("c-03"))
    for event in run.events:
        if event["type"] == "task_result" and event["payload"].get("task_id") == "T3-07":
            event["ground_truth"] = {"forced": False}
    text = render_report([run], "memory")
    assert "FAIL" in text
    assert "Confirmatory output refused" in text
    assert "significant" not in text
    assert "Fisher p=" not in text


def test_step_sequence_mismatch_is_unblinded_and_blocks_contrasts():
    run = build_run(spec_by_id("c-01"))
    for event in run.events:
        payload = event.get("payload") or {}
        if payload.get("template_id"):
            payload["template_id"] = "deadbeef00"
    text = render_report([run], "memory")
    assert text.index("## UNBLINDED") < text.index("Step-sequence check FAIL")
    assert "Confirmatory output refused" in text
    assert "Fisher p=" not in text


def test_aborted_runs_are_excluded_and_budget_env_is_echoed(monkeypatch):
    run = build_run(spec_by_id("c-03"))
    run.meta["aborted"] = "budget"
    monkeypatch.setenv("COOP_BUDGET_USD", "12.5")
    text = render_report([run], "memory")
    assert "Excluded because meta.aborted is set: c-03 (budget)" in text
    assert "COOP_BUDGET_USD=12.5" in text
    assert "No confirmatory runs." in text


def test_preregistered_test_helper_writes_the_two_fisher_tests(tmp_path):
    path = tmp_path / "record.jsonl"
    entry = log_preregistered_tests(path)
    assert entry["kind"] == "preregistered_tests"
    assert entry["cell"] == {"access": "earned", "visibility": "deliverable_only"}
    assert [item["name"] for item in entry["tests"]] == [
        "recruiter_vs_null",
        "null_vs_pressure_only",
    ]
    assert entry["secondary"] == ["recruiter_vs_pressure_only"]
    assert "ts" in entry
    # Rendering a report must not append another prereg line.
    render_report(catalog_runs(), "memory")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_cli_exit_codes_and_no_api_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COOP_RECORD_PATH", str(tmp_path / "record.jsonl"))
    assert main([]) == 2
    assert main([str(tmp_path / "missing")]) == 2
    write_catalog(tmp_path / "runs")
    assert main([str(tmp_path / "runs")]) == 0
    broken = tmp_path / "runs" / "c-09" / "meta.json"
    meta = json.loads(broken.read_text(encoding="utf-8"))
    meta["mode"] = "improvised"
    broken.write_text(json.dumps(meta), encoding="utf-8")
    assert main([str(tmp_path / "runs")]) == 1


def test_module_entrypoint_is_offline(tmp_path):
    write_catalog(tmp_path)
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONPATH"] = str(ROOT)
    env["COOP_RECORD_PATH"] = str(tmp_path / "record.jsonl")
    report = subprocess.run(
        [sys.executable, "-m", "coop.eval.report", str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    assert "UNBLINDED" in report.stdout
    assert "significant" not in report.stdout.split("## Exploratory", 1)[1]
    recovery = subprocess.run(
        [sys.executable, "-m", "coop.eval.objective_recovery", str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert recovery.returncode == 0, recovery.stderr
    assert recovery.stdout.startswith("skipped")
    assert "UNBLINDED" in recovery.stdout
    assert "accuracy=1.000" in recovery.stdout
    logged = (tmp_path / "record.jsonl").read_text(encoding="utf-8")
    assert logged.count("blind_prediction") == 9


def test_write_run_round_trip_used_by_cli(tmp_path):
    write_run(tmp_path / "one", catalog_runs()[0])
    assert (tmp_path / "one" / "sealed.json").is_file()
