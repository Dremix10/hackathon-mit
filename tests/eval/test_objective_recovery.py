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


def test_baseline_beats_chance_on_fixtures(tmp_path):
    labeled = [run for run in catalog_runs() if run.sealed is not None]
    result = score_runs(labeled, baseline_predictor, record_path=tmp_path / "record.jsonl")
    assert result.n == 9
    assert result.accuracy == 1.0
    assert result.accuracy > result.chance_majority
    assert result.chance_uniform == pytest.approx(0.25)
    assert result.chance_majority == pytest.approx(3 / 9)
    labels = result.labels
    for index, label in enumerate(labels):
        assert result.matrix[index][index] == result.truths.count(label)
        assert sum(result.matrix[index]) == result.truths.count(label)
    text = format_recovery(result)
    assert text.startswith("UNBLINDED")
    assert "accuracy=1.000" in text


def test_predictor_receives_no_label_and_cannot_mutate_the_run(tmp_path):
    run = next(item for item in catalog_runs() if item.sealed["recruiter_objective"] == "loyalty")
    seen = {}

    def spy(events, meta):
        seen["keys"] = set(meta)
        seen["n_events"] = len(events)
        events.append({"type": "message"})
        meta["recruiter_objective"] = "loyalty"
        return "loyalty"

    before = len(run.events)
    result = score_runs([run], spy, record_path=tmp_path / "record.jsonl")
    assert result.accuracy == 1.0
    assert "recruiter_objective" not in seen["keys"]
    assert "insider_id" in seen["keys"]
    assert len(run.events) == before
    assert "recruiter_objective" not in run.meta


def test_baseline_does_not_read_mnemonic_run_ids(tmp_path):
    labeled = []
    for index, run in enumerate(catalog_runs()):
        if run.sealed is None:
            continue
        opaque = f"opaque-{index}"
        run.run_id = opaque
        run.meta["run_id"] = opaque
        for event in run.events:
            event["run_id"] = opaque
        if run.sealed is not None:
            run.sealed["run_id"] = opaque
            from coop.eval.validate import canonical_sealed_bytes, sha256_hex

            run.meta["sealed_sha256"] = sha256_hex(canonical_sealed_bytes(run.sealed))
        labeled.append(run)
    assert (
        score_runs(labeled, baseline_predictor, record_path=tmp_path / "record.jsonl").accuracy
        == 1.0
    )


def test_unlabeled_runs_are_refused(tmp_path):
    pressure = next(run for run in catalog_runs() if run.run_id == "c-09")
    with pytest.raises(ValueError, match="sealed"):
        score_runs([pressure], baseline_predictor, record_path=tmp_path / "record.jsonl")


def test_prediction_is_logged_before_sealed_bytes_are_read(tmp_path, monkeypatch):
    write_catalog(tmp_path / "runs")
    record = tmp_path / "record.jsonl"
    order = []

    def spy(events, meta):
        order.append("predict")
        assert "recruiter_objective" not in meta
        blob = json.dumps(events)
        assert "resource_capture" not in blob
        return "null"

    from coop.eval import objective_recovery

    real = objective_recovery._read_sealed_bytes

    def wrapped(path):
        order.append("unblind")
        assert record.is_file()
        logged = record.read_text(encoding="utf-8")
        assert "blind_prediction" in logged
        assert "c-01" in logged
        return real(path)

    monkeypatch.setattr(objective_recovery, "_read_sealed_bytes", wrapped)
    result = score_run_dirs([tmp_path / "runs" / "c-01"], spy, record_path=record)
    assert order == ["predict", "unblind"]
    assert result.accuracy == 1.0
    assert result.truths == ("null",)


def test_hash_mismatch_refuses_to_score_after_the_prediction_is_logged(tmp_path):
    write_catalog(tmp_path / "runs")
    record = tmp_path / "record.jsonl"
    meta_path = tmp_path / "runs" / "c-01" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sealed_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to score"):
        score_run_dirs(
            [tmp_path / "runs" / "c-01"],
            baseline_predictor,
            record_path=record,
        )
    logged = record.read_text(encoding="utf-8")
    assert "blind_prediction" in logged
    assert "c-01" in logged
