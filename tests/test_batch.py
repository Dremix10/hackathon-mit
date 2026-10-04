"""Blinded batch summary and sealed arm file."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from coop.schema import RunConfig
from coop.sim.batch import main
from coop.sim.seeds import write_freeze


def test_batch_summary_hides_the_arm(tmp_path: Path):
    registry = {"tuning": [1], "heldout": [101, 102, 103]}
    reg_path = tmp_path / "seeds.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")
    freeze = tmp_path / "frozen.json"
    write_freeze(RunConfig(seed=101, mode="pressure_only"), freeze)
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
    assert {row["seed"] for row in rows} == {"101", "102"}
    assert {row["seed_split"] for row in rows} == {"heldout"}
    assert all(row["status"] == "complete" for row in rows)
    assert all(row["y"] in {"0", "1"} for row in rows)
    sealed = (out / "sealed_summary.csv").read_text(encoding="utf-8")
    assert "loyalty" in sealed
    assert "null" in sealed
    with (out / "sealed_summary.csv").open(encoding="utf-8", newline="") as handle:
        sealed_rows = list(csv.DictReader(handle))
    assert {(row["arm"], row["seed"]) for row in sealed_rows} == {
        ("loyalty", "101"),
        ("loyalty", "102"),
        ("null", "101"),
        ("null", "102"),
    }


def test_batch_resume_skips_completed_pairs(tmp_path: Path):
    registry = {"tuning": [1], "heldout": [101, 102]}
    reg_path = tmp_path / "seeds.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")
    freeze = tmp_path / "frozen.json"
    write_freeze(RunConfig(seed=101, mode="pressure_only"), freeze)
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
