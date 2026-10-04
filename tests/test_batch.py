"""Blinded batch summary and sealed arm file."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from coop.eval.outcomes import primary_outcome as section6_outcome
from coop.schema import RunConfig
from coop.sim.batch import main
from coop.sim.seeds import write_freeze


def _write_registry(path: Path, dev: list[int], held: list[int]) -> None:
    path.write_text(
        json.dumps(
            {
                "dev": {"start": 0, "end": 999, "pool": dev},
                "held_out": {"start": 1000, "pool": held},
            }
        ),
        encoding="utf-8",
    )


def test_batch_summary_hides_the_arm(tmp_path: Path):
    reg_path = tmp_path / "seeds.json"
    _write_registry(reg_path, [1], [1000, 1001, 1002])
    freeze = tmp_path / "frozen.json"
    write_freeze(RunConfig(seed=1000, mode="pressure_only"), freeze)
    out = tmp_path / "runs"
    code = main(
        [
            "--arms",
            "loyalty,null",
            "--split",
            "heldout",
            "--n",
            "2",
            "--backend",
            "mock",
            "--out",
            str(out),
            "--registry",
            str(reg_path),
            "--seed-log",
            str(tmp_path / "log.jsonl"),
            "--freeze",
            str(freeze),
            "--profile",
            "comply_all",
        ]
    )
    assert code == 0
    summary = (out / "summary.csv").read_text(encoding="utf-8")
    assert "loyalty" not in summary
    assert "null" not in summary
    assert "resource_capture" not in summary
    with (out / "summary.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    assert {row["seed"] for row in rows} == {"1000", "1001"}
    assert {row["seed_split"] for row in rows} == {"held_out"}
    assert all(row["status"] == "complete" for row in rows)
    assert all(row["y"] in {"0", "1"} for row in rows)
    assert all(row["y_tstar2"] in {"0", "1"} for row in rows)
    assert all(row["n_violations"].isdigit() for row in rows)
    for row in rows:
        events = [
            json.loads(line)
            for line in (out / row["run_id"] / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        meta = json.loads((out / row["run_id"] / "meta.json").read_text(encoding="utf-8"))
        assert row["y"] == str(int(section6_outcome(events, meta)))
    sealed = (out / "sealed_summary.csv").read_text(encoding="utf-8")
    assert "loyalty" in sealed
    assert "null" in sealed
    with (out / "sealed_summary.csv").open(encoding="utf-8", newline="") as handle:
        sealed_rows = list(csv.DictReader(handle))
    assert {(row["arm"], row["seed"]) for row in sealed_rows} == {
        ("loyalty", "1000"),
        ("loyalty", "1001"),
        ("null", "1000"),
        ("null", "1001"),
    }


def test_batch_resume_skips_completed_pairs(tmp_path: Path):
    reg_path = tmp_path / "seeds.json"
    _write_registry(reg_path, [1], [1000, 1001])
    freeze = tmp_path / "frozen.json"
    write_freeze(RunConfig(seed=1000, mode="pressure_only"), freeze)
    out = tmp_path / "runs"
    args = [
        "--arms",
        "pressure_only",
        "--split",
        "heldout",
        "--n",
        "1",
        "--backend",
        "mock",
        "--out",
        str(out),
        "--registry",
        str(reg_path),
        "--seed-log",
        str(tmp_path / "log.jsonl"),
        "--freeze",
        str(freeze),
    ]
    assert main(args) == 0
    first = (out / "summary.csv").read_text(encoding="utf-8")
    assert main(args + ["--resume"]) == 0
    assert (out / "summary.csv").read_text(encoding="utf-8") == first
    assert len((tmp_path / "log.jsonl").read_text().splitlines()) == 1
