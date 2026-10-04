"""Held-out registry, freeze guard, and the behavior hash."""

from __future__ import annotations

import json
from pathlib import Path

from coop.schema import ConfigError, RunConfig
from coop.sim.run import main
from coop.sim.seeds import (
    DEFAULT_FREEZE,
    DEFAULT_REGISTRY,
    append_log,
    check_heldout,
    config_sha256,
    load_registry,
    resolve_seed,
    write_freeze,
)


def test_committed_pools_are_disjoint_and_heldout_has_two():
    registry = load_registry(DEFAULT_REGISTRY)
    assert set(registry["tuning"]).isdisjoint(registry["heldout"])
    assert len(registry["heldout"]) >= 2
    assert len(registry["tuning"]) >= 2


def test_seed_must_belong_to_a_declared_split(tmp_path: Path):
    registry = {"tuning": [1, 2], "heldout": [101, 102]}
    log = tmp_path / "log.jsonl"
    try:
        resolve_seed(seed=99, split=None, registry=registry, log_path=log)
    except ConfigError as exc:
        assert "not in a declared split" in str(exc)
    else:
        raise AssertionError("undeclared seed was accepted")
    try:
        resolve_seed(seed=1, split="heldout", registry=registry, log_path=log)
    except ConfigError as exc:
        assert "belongs to tuning" in str(exc)
    else:
        raise AssertionError("seed was accepted on the wrong split")


def test_split_draws_the_next_unused_seed(tmp_path: Path):
    registry = {"tuning": [1], "heldout": [101, 102]}
    log = tmp_path / "log.jsonl"
    first, split = resolve_seed(seed=None, split="heldout", registry=registry, log_path=log)
    assert (first, split) == (101, "heldout")
    append_log(log, run_id="c-0001", seed=first, split=split, config_digest="abc")
    second, _ = resolve_seed(seed=None, split="heldout", registry=registry, log_path=log)
    assert second == 102
    row = json.loads(log.read_text().splitlines()[0])
    assert set(row) >= {"run_id", "seed", "split", "config_sha256", "timestamp"}


def test_config_hash_ignores_the_arm_and_changes_with_temperature():
    loyalty = config_sha256(
        RunConfig(seed=1, mode="controlled", recruiter_objective="loyalty", profile="comply_all")
    )
    null = config_sha256(
        RunConfig(seed=101, mode="controlled", recruiter_objective="null", profile="refuse_all")
    )
    assert loyalty == null
    warmer = config_sha256(
        RunConfig(seed=1, mode="controlled", recruiter_objective="loyalty", temperature=0.2)
    )
    assert warmer != loyalty


def test_heldout_requires_the_frozen_hash(tmp_path: Path):
    freeze = tmp_path / "frozen.json"
    base = RunConfig(seed=101, mode="pressure_only", seed_split="heldout")
    write_freeze(base, freeze)
    check_heldout(base, freeze)
    drifted = RunConfig(seed=101, mode="pressure_only", seed_split="heldout", temperature=0.4)
    try:
        check_heldout(drifted, freeze)
    except ConfigError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("drifted held-out config was accepted")
    drifted.allow_unfrozen = True
    check_heldout(drifted, freeze)
    missing = RunConfig(seed=101, mode="pressure_only", seed_split="heldout")
    try:
        check_heldout(missing, tmp_path / "absent.json")
    except ConfigError as exc:
        assert "no frozen config" in str(exc)
    else:
        raise AssertionError("missing freeze was accepted")


def test_cli_records_split_hash_and_unfrozen_flag(tmp_path: Path):
    freeze = tmp_path / "frozen.json"
    write_freeze(RunConfig(seed=1, mode="pressure_only"), freeze)
    log = tmp_path / "log.jsonl"
    code = main(
        [
            "--split",
            "heldout",
            "--mode",
            "pressure_only",
            "--allow-unfrozen",
            "--temperature",
            "0.3",
            "--out",
            str(tmp_path / "runs"),
            "--seed-log",
            str(log),
            "--freeze",
            str(freeze),
            "--run-id",
            "c-hold",
        ]
    )
    assert code == 0
    meta = json.loads((tmp_path / "runs" / "c-hold" / "meta.json").read_text())
    assert meta["seed_split"] == "heldout"
    assert meta["seed"] == 101
    assert meta["allow_unfrozen"] is True
    assert meta["config_sha256"]
    assert meta["status"] == "complete"
    assert "recruiter_objective" not in meta
    logged = json.loads(log.read_text().splitlines()[0])
    assert logged["seed"] == 101
    assert logged["config_sha256"] == meta["config_sha256"]
    assert logged["allow_unfrozen"] is True


def test_cli_blocks_unfrozen_heldout(tmp_path: Path, capsys):
    code = main(
        [
            "--seed",
            "101",
            "--mode",
            "pressure_only",
            "--temperature",
            "0.9",
            "--out",
            str(tmp_path),
            "--seed-log",
            str(tmp_path / "log.jsonl"),
            "--freeze",
            str(tmp_path / "missing.json"),
        ]
    )
    assert code == 2
    assert "frozen" in capsys.readouterr().err


def test_committed_freeze_matches_the_default_config():
    frozen = json.loads(DEFAULT_FREEZE.read_text())
    assert frozen["config_sha256"] == config_sha256(RunConfig(seed=1, mode="pressure_only"))
