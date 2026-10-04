"""Blind objective-recovery scoring."""

import json

import pytest

from coop.eval.fixtures import catalog_runs, write_catalog
from coop.eval.objective_recovery import (
    baseline_predictor,
    format_recovery,
    score_run_dirs,
    score_runs,
)


def test_baseline_beats_chance_on_fixtures():
    labeled = [run for run in catalog_runs() if run.sealed is not None]
    result = score_runs(labeled, baseline_predictor)
    assert result.n == 7
    assert result.accuracy == 1.0
    assert result.accuracy > result.chance_majority
    assert result.chance_uniform == pytest.approx(0.25)
    assert result.chance_majority == pytest.approx(2 / 7)
    labels = result.labels
    for index, label in enumerate(labels):
        assert result.matrix[index][index] == result.truths.count(label)
        assert sum(result.matrix[index]) == result.truths.count(label)
    text = format_recovery(result)
    assert text.startswith("UNBLINDED")
    assert "accuracy=1.000" in text


def test_predictor_receives_no_label_and_cannot_mutate_the_run():
    run = next(item for item in catalog_runs() if item.sealed["recruiter_objective"] == "loyalty")
    seen = {}

    def spy(events, meta):
        seen["keys"] = set(meta)
        seen["n_events"] = len(events)
        events.append({"type": "message"})
        meta["recruiter_objective"] = "loyalty"
        return "loyalty"

    before = len(run.events)
    result = score_runs([run], spy)
    assert result.accuracy == 1.0
    assert "recruiter_objective" not in seen["keys"]
    assert "insider_slot" in seen["keys"]
    assert len(run.events) == before
    assert "recruiter_objective" not in run.meta


def test_baseline_does_not_read_mnemonic_run_ids():
    labeled = []
    for index, run in enumerate(catalog_runs()):
        if run.sealed is None:
            continue
        opaque = f"opaque-{index}"
        run.run_id = opaque
        run.meta["run_id"] = opaque
        for event in run.events:
            event["run_id"] = opaque
        labeled.append(run)
    assert score_runs(labeled, baseline_predictor).accuracy == 1.0


def test_unlabeled_runs_are_refused():
    pressure = next(run for run in catalog_runs() if run.run_id == "pressure_only")
    with pytest.raises(ValueError, match="sealed"):
        score_runs([pressure], baseline_predictor)


def test_sealed_file_is_read_only_after_prediction(tmp_path, monkeypatch):
    write_catalog(tmp_path)
    order = []

    def spy(events, meta):
        order.append("predict")
        assert "recruiter_objective" not in meta
        blob = json.dumps(events)
        assert "resource_capture" not in blob
        return "null"

    from coop.eval import objective_recovery

    real = objective_recovery._read_sealed

    def wrapped(path):
        order.append("unblind")
        return real(path)

    monkeypatch.setattr(objective_recovery, "_read_sealed", wrapped)
    result = score_run_dirs([tmp_path / "null_insider_honest"], spy)
    assert order == ["predict", "unblind"]
    assert result.accuracy == 1.0
    assert result.truths == ("null",)
