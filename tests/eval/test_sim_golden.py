"""Report and checks against vendored simulator outputs from PR #5.

The files are a copy of ``runs/examples`` on ``cursor/coop-sim-7b54``. They are
not produced by this package. A bare ``.jsonl`` is an events-only golden; ``c-0001``
is a full controlled run directory.
"""

from pathlib import Path

from coop.eval.blinding import blinding_problems
from coop.eval.checks import blind_manipulation_problems, step_sequence_problems
from coop.eval.report import main, render_report
from coop.eval.validate import load_run, load_runs

GOLDEN = Path(__file__).resolve().parent / "golden"


def test_controlled_sample_passes_validator_checks_and_blinding():
    run = load_run(GOLDEN / "c-0001")
    assert run.ok, run.problems
    assert run.meta["planned_failure"]["tstar"] == "T3-03"
    assert run.meta["planned_failure"]["tstar_owner"] == "a1"
    assert run.sealed["recruiter_objective"] == "loyalty"
    assert "true_results" in next(
        event["ground_truth"] for event in run.events if event["type"] == "report_submitted"
    )
    assert blind_manipulation_problems([run]) == []
    assert step_sequence_problems(run.events, run.meta, run.sealed) == []
    assert blinding_problems(GOLDEN / "c-0001") == []


def test_events_only_jsonl_loads_and_the_report_runs():
    blame = load_run(GOLDEN / "mock_blame_avoidance_comply_all.jsonl")
    assert blame.ok, blame.problems
    assert blame.meta["events_only"] is True
    assert any(event["type"] == "violation" for event in blame.events)
    runs = load_runs(GOLDEN)
    ids = {run.run_id for run in runs}
    assert "c-0001" in ids
    assert "mock_null_refuse_all" in ids
    assert "mock_blame_avoidance_comply_all" in ids
    assert "mock_loyalty_refuse_all" in ids
    text = render_report(runs, GOLDEN)
    assert "PASS" in text.split("## UNBLINDED", 1)[0]
    assert "UNBLINDED" in text
    assert "mock_blame_avoidance_comply_all" in text
    assert "Events-only logs" in text
    assert "Confirmatory output refused" not in text
    assert main([str(GOLDEN)]) == 0
