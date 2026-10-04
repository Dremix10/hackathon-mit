"""Report CLI: preregistered contrasts only, condition read from meta."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from coop.eval.fixtures import build_run, catalog_runs, spec_by_id, write_catalog, write_run
from coop.eval.report import main, render_report, significance_call

ROOT = Path(__file__).resolve().parents[2]


def _split(text: str) -> tuple[str, str, str]:
    before, after = text.split("## UNBLINDED", 1)
    arms, contrasts = after.split("## Preregistered contrasts", 1)
    _contrasts, exploratory = contrasts.split("## Exploratory", 1)
    return before, arms, exploratory


def test_catalog_report_groups_meta_conditions_and_unblinds(tmp_path):
    write_catalog(tmp_path)
    from coop.eval.validate import load_runs

    text = render_report(load_runs(tmp_path), tmp_path)
    before, arms, exploratory = _split(text)
    assert "UNBLINDED" in text
    assert "pressure_only" in before
    assert "no_conflict" in before
    assert "Wilson" in before
    assert "earned_deliverable" in before
    assert "significant" not in before
    assert "significant" not in arms
    assert "significant" not in exploratory
    assert "reset_on_removal" in exploratory
    assert "Newcomer" in exploratory
    assert "spontaneous" in exploratory
    assert "earned x deliverable" in exploratory
    assert "Runs not in the three-arm contrast: spontaneous" in text
    assert "recruiter_objective" in text  # named in the UNBLINDED explanation


def test_custom_condition_is_grouped_from_meta_not_a_hardcoded_list():
    spec = replace(
        spec_by_id("spontaneous"),
        run_id="custom",
        condition="custom_cell",
        mode="controlled",
    )
    text = render_report([build_run(spec)], "memory")
    assert "custom_cell" in text
    assert "significant" not in text.split("## Exploratory", 1)[1]


def test_only_preregistered_contrasts_can_be_called_significant(tmp_path):
    runs = []
    for index in range(6):
        runs.append(
            build_run(
                replace(spec_by_id("omitted_failure"), run_id=f"rec-{index}", seed=3000 + index)
            )
        )
        runs.append(
            build_run(
                replace(
                    spec_by_id("null_insider_honest"),
                    run_id=f"null-{index}",
                    seed=4000 + index,
                )
            )
        )
        runs.append(
            build_run(
                replace(spec_by_id("pressure_only"), run_id=f"press-{index}", seed=5000 + index)
            )
        )
        runs.append(
            build_run(
                replace(
                    spec_by_id("omitted_failure"),
                    run_id=f"high-{index}",
                    seed=6000 + index,
                    condition="cell_high",
                )
            )
        )
        runs.append(
            build_run(
                replace(
                    spec_by_id("null_insider_honest"),
                    run_id=f"low-{index}",
                    seed=7000 + index,
                    condition="cell_low",
                )
            )
        )
    text = render_report(runs, tmp_path)
    before, arms, exploratory = _split(text)
    assert "cell_high" in before
    assert "cell_low" in before
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
    with pytest_raises_value():
        significance_call("cell_high", "cell_low", 0.001)
    try:
        significance_call("null_insider", "recruiter", 0.001)
    except ValueError as exc:
        assert "not a preregistered contrast" in str(exc)
    else:
        raise AssertionError("reversed pair should be refused")
    assert significance_call("recruiter", "null_insider", 0.001) == "significant"
    assert significance_call("recruiter", "pressure_only", 0.2) == "not significant"


def pytest_raises_value():
    import pytest

    return pytest.raises(ValueError, match="not a preregistered contrast")


def test_aborted_runs_are_excluded_and_budget_env_is_echoed(monkeypatch):
    run = build_run(spec_by_id("omitted_failure"))
    run.meta["aborted"] = "budget"
    monkeypatch.setenv("COOP_BUDGET_USD", "12.5")
    text = render_report([run], "memory")
    assert "Excluded because meta.aborted is set: omitted_failure (budget)" in text
    assert "COOP_BUDGET_USD=12.5" in text
    assert "No confirmatory runs." in text


def test_cli_exit_codes_and_no_api_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main([]) == 2
    assert main([str(tmp_path / "missing")]) == 2
    write_catalog(tmp_path / "runs")
    assert main([str(tmp_path / "runs")]) == 0
    broken = tmp_path / "runs" / "pressure_only" / "meta.json"
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
    assert recovery.stdout.startswith("skipped") or recovery.stdout.splitlines()[0].startswith(
        "skipped"
    )
    assert "UNBLINDED" in recovery.stdout
    assert "accuracy=1.000" in recovery.stdout


def test_write_run_round_trip_used_by_cli(tmp_path):
    # Imported so a partial catalog still satisfies the loader. Keeps write_run covered.
    write_run(tmp_path / "one", catalog_runs()[0])
    assert (tmp_path / "one" / "sealed.json").is_file()
