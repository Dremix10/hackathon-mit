"""Known-ground-truth traces."""

from pathlib import Path

from coop.eval.blinding import blinding_problems
from coop.eval.checks import blind_manipulation_problems, step_sequence_problems
from coop.eval.fixtures import (
    CATALOG,
    catalog_runs,
    observation_hash,
    write_catalog,
)
from coop.eval.outcomes import (
    detector_mismatches,
    is_aggregate_only,
    newcomer_outcome,
    persistence_outcome,
    primary_outcome,
    primary_outcome_dprime,
)
from coop.eval.contract import CHANNELS, EVENT_TYPES

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "coop" / "eval" / "examples"

EXPECTED_Y = {
    "c-01": False,
    "c-02": False,
    "c-03": True,
    "c-04": True,
    "c-05": True,
    "c-06": False,
    "c-07": True,
    "c-08": False,
    "c-09": False,
    "c-10": False,
    "c-11": False,
    "c-12": False,
}


def test_catalog_is_valid_and_matches_known_outcomes():
    runs = catalog_runs()
    assert [run.run_id for run in runs] == [spec.run_id for spec in CATALOG]
    for run in runs:
        assert run.ok, run.problems
    by_id = {run.run_id: run for run in runs}
    for run_id, expected in EXPECTED_Y.items():
        assert primary_outcome(by_id[run_id].events, by_id[run_id].meta) is expected
        assert detector_mismatches(by_id[run_id].events) == []
    assert primary_outcome_dprime(by_id["c-07"].events, by_id["c-07"].meta) is False
    assert primary_outcome_dprime(by_id["c-03"].events, by_id["c-03"].meta) is True
    assert is_aggregate_only(by_id["c-08"].events) is True
    assert is_aggregate_only(by_id["c-03"].events) is False
    assert persistence_outcome(by_id["c-05"].events, by_id["c-05"].meta) is True
    assert persistence_outcome(by_id["c-06"].events, by_id["c-06"].meta) is False
    assert persistence_outcome(by_id["c-09"].events, by_id["c-09"].meta) is False
    assert newcomer_outcome(by_id["c-05"].events, by_id["c-05"].meta) is True
    assert newcomer_outcome(by_id["c-09"].events, by_id["c-09"].meta) is False
    assert by_id["c-06"].meta["reset_on_removal"] is True
    assert by_id["c-09"].meta["mode"] == "pressure_only"
    assert by_id["c-09"].meta["condition"] == {
        "access": "earned",
        "visibility": "deliverable_only",
    }
    assert by_id["c-12"].meta["no_conflict"] is True
    assert by_id["c-10"].meta["mode"] == "spontaneous"
    assert by_id["c-09"].meta["insider_id"] is None
    assert "sealed_sha256" not in by_id["c-09"].meta
    assert by_id["c-01"].meta["insider_id"] == "a0"
    assert blind_manipulation_problems(runs) == []
    for run in runs:
        if run.meta.get("mode") == "controlled":
            assert step_sequence_problems(run.events, run.meta, run.sealed) == []


def test_hidden_objective_is_not_in_events_or_meta():
    forbidden = {"resource_capture", "blame_avoidance", "loyalty"}
    for run in catalog_runs():
        assert "recruiter_objective" not in run.meta
        found = _string_values(run.events) | _string_values(run.meta)
        assert forbidden.isdisjoint(found)
        if run.sealed is not None:
            assert run.sealed["recruiter_objective"] not in found


def test_traces_cover_the_schema_and_hash_their_observations():
    seen_types = set()
    seen_channels = set()
    for run in catalog_runs():
        for event in run.events:
            seen_types.add(event["type"])
            seen_channels.add(event["channel"])
            if event["type"] == "observation":
                assert event["payload"]["hash"] == observation_hash(event["payload"]["observation"])
    assert seen_types == EVENT_TYPES
    assert seen_channels == CHANNELS


def test_checked_in_examples_match_the_builder(tmp_path):
    write_catalog(tmp_path)
    expected_ids = {spec.run_id for spec in CATALOG}
    found_ids = {path.name for path in EXAMPLES.iterdir() if path.is_dir()}
    assert found_ids == expected_ids
    for run_id in expected_ids:
        for name in ("events.jsonl", "meta.json"):
            assert (EXAMPLES / run_id / name).read_text(encoding="utf-8") == (
                tmp_path / run_id / name
            ).read_text(encoding="utf-8")
        sealed = EXAMPLES / run_id / "sealed.json"
        regenerated = tmp_path / run_id / "sealed.json"
        assert sealed.exists() == regenerated.exists()
        if sealed.exists():
            assert sealed.read_bytes() == regenerated.read_bytes()
        assert blinding_problems(EXAMPLES / run_id) == []


def _string_values(value):
    found = set()
    if isinstance(value, str):
        found.add(value)
    elif isinstance(value, dict):
        found.update(_string_values(list(value.values())))
    elif isinstance(value, list):
        for item in value:
            found.update(_string_values(item))
    return found
