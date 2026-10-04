"""Arm labels stay in sealed.json. Observations carry no template-map keys."""

from __future__ import annotations

import json
from pathlib import Path

from coop.schema import RunConfig
from coop.sim.env import CoopSim


ARM_STRINGS = ("resource_capture", "blame_avoidance", "loyalty")


def _run(tmp: Path, objective: str) -> Path:
    sim = CoopSim(
        RunConfig(
            seed=11,
            mode="controlled",
            access="earned",
            visibility="deliverable_only",
            recruiter_objective=objective,
            profile="comply_all",
            run_id="c-0011",
        )
    )
    sim.play()
    return sim.write(tmp / objective)


def test_public_files_hide_the_objective(tmp_path: Path):
    for objective in ("resource_capture", "blame_avoidance", "loyalty", "null"):
        run_dir = _run(tmp_path, objective)
        sealed = json.loads((run_dir / "sealed.json").read_text())
        assert sealed["recruiter_objective"] == objective
        keys = set(sealed["template_map"])
        for path in run_dir.iterdir():
            if path.name == "sealed.json":
                continue
            text = path.read_text()
            assert "recruiter_objective" not in text
            for arm in ARM_STRINGS:
                assert arm not in text, (objective, path.name, arm)
            # The arm name null is a JSON string, not the JSON literal null.
            assert '"null"' not in text
            for key in keys:
                start = 0
                while True:
                    at = text.find(key, start)
                    if at < 0:
                        break
                    window = text[max(0, at - 20) : at]
                    assert "template_id" in window, (path.name, key, window)
                    start = at + len(key)


def test_observations_have_no_arm_or_map_keys(tmp_path: Path):
    run_dir = _run(tmp_path, "loyalty")
    sealed = json.loads((run_dir / "sealed.json").read_text())
    keys = set(sealed["template_map"])
    for line in (run_dir / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        if event["type"] != "observation":
            continue
        blob = json.dumps(event["payload"])
        for arm in ARM_STRINGS:
            assert arm not in blob
        assert '"null"' not in blob
        for key in keys:
            assert key not in blob


def test_pressure_only_has_no_sealed_file(tmp_path: Path):
    sim = CoopSim(
        RunConfig(
            seed=11,
            mode="pressure_only",
            access="earned",
            visibility="deliverable_only",
            profile="refuse_all",
            run_id="c-press",
        )
    )
    sim.play()
    path = sim.write(tmp_path / "pressure")
    assert not (path / "sealed.json").exists()
    meta = json.loads((path / "meta.json").read_text())
    assert meta["insider_id"] is None
    assert "sealed_sha256" not in meta
    assert meta["mode"] == "pressure_only"
