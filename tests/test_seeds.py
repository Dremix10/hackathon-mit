"""Held-out registry, freeze guard, and the behavior hash."""

from __future__ import annotations

import json
from pathlib import Path

from coop.eval.discoveries import classify_seed, load_seed_split
from coop.schema import ConfigError, RunConfig
from coop.sim.run import main
from coop.sim.seeds import (
    DEFAULT_FREEZE,
    DEFAULT_REGISTRY,
    HASH_FIELDS,
    SeedRegistry,
    append_log,
    behavior_material,
    check_heldout,
    config_sha256,
    load_registry,
    resolve_seed,
    sim_code_sha256,
    split_of,
    write_freeze,
)


def _registry(dev: tuple[int, ...] = (1, 2), held: tuple[int, ...] = (1000, 1001)) -> SeedRegistry:
    return SeedRegistry(0, 999, 1000, {"dev": dev, "held_out": held})


def test_committed_ranges_and_pools_match_the_ledger_file():
    raw = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    assert raw["dev"]["start"] == 0
    assert raw["dev"]["end"] == 999
    assert raw["dev"]["use"] == "exploration, tuning, and pilots"
    assert raw["held_out"]["start"] == 1000
    assert raw["held_out"]["use"] == "confirmation only"
    registry = load_registry(DEFAULT_REGISTRY)
    assert registry.pools["dev"] == tuple(range(1, 41))
    assert registry.pools["held_out"] == tuple(range(1000, 1040))
    assert set(registry.pools["dev"]).isdisjoint(registry.pools["held_out"])
    disc = load_seed_split(DEFAULT_REGISTRY)
    for seed in (0, 1, 40, 50, 999, 1000, 1039, 5000):
        assert split_of(seed, registry) == classify_seed(seed, disc)
    assert split_of(-1, registry) is None


def test_seed_must_belong_to_a_declared_split(tmp_path: Path):
    registry = SeedRegistry(0, 10, 1000, {"dev": (1, 2), "held_out": (1000, 1001)})
    log = tmp_path / "log.jsonl"
    try:
        resolve_seed(seed=50, split=None, registry=registry, log_path=log)
    except ConfigError as exc:
        assert "not in a declared split" in str(exc)
    else:
        raise AssertionError("undeclared seed was accepted")
    try:
        resolve_seed(seed=1, split="held_out", registry=registry, log_path=log)
    except ConfigError as exc:
        assert "belongs to dev" in str(exc)
    else:
        raise AssertionError("seed was accepted on the wrong split")
    seed, split = resolve_seed(seed=50, split=None, registry=_registry(), log_path=log)
    assert (seed, split) == (50, "dev")


def test_split_draws_the_next_unused_seed(tmp_path: Path):
    registry = _registry(dev=(1,), held=(1000, 1001))
    log = tmp_path / "log.jsonl"
    first, split = resolve_seed(seed=None, split="heldout", registry=registry, log_path=log)
    assert (first, split) == (1000, "held_out")
    append_log(log, run_id="c-0001", seed=first, split=split, config_digest="abc")
    second, _ = resolve_seed(seed=None, split="held_out", registry=registry, log_path=log)
    assert second == 1001
    row = json.loads(log.read_text().splitlines()[0])
    assert set(row) >= {"run_id", "seed", "split", "config_sha256", "timestamp"}
    assert row["split"] == "held_out"


def test_config_hash_ignores_arm_period_and_changes_with_model():
    loyalty = config_sha256(
        RunConfig(seed=1, mode="controlled", recruiter_objective="loyalty", profile="comply_all")
    )
    null = config_sha256(
        RunConfig(seed=1000, mode="controlled", recruiter_objective="null", profile="refuse_all")
    )
    assert loyalty == null
    wider = config_sha256(
        RunConfig(seed=1, mode="controlled", recruiter_objective="loyalty", period_size=9, access="routine")
    )
    assert wider == loyalty
    other_model = config_sha256(
        RunConfig(seed=1, mode="controlled", recruiter_objective="loyalty", model="other")
    )
    assert other_model != loyalty
    material = behavior_material(RunConfig(seed=1, mode="pressure_only"))
    assert tuple(material) == HASH_FIELDS
    assert material["sim_code_sha256"] == sim_code_sha256()
    assert "sim_git_sha" not in material


def test_heldout_requires_the_frozen_hash(tmp_path: Path):
    freeze = tmp_path / "frozen.json"
    base = RunConfig(seed=1000, mode="pressure_only", seed_split="held_out")
    write_freeze(base, freeze)
    check_heldout(base, freeze)
    drifted = RunConfig(seed=1000, mode="pressure_only", seed_split="held_out", temperature=0.4)
    try:
        check_heldout(drifted, freeze)
    except ConfigError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("drifted held-out config was accepted")
    drifted.allow_unfrozen = True
    check_heldout(drifted, freeze)
    missing = RunConfig(seed=1000, mode="pressure_only", seed_split="held_out")
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
    assert meta["seed_split"] == "held_out"
    assert meta["seed"] == 1000
    assert meta["allow_unfrozen"] is True
    assert meta["config_sha256"]
    assert meta["status"] == "complete"
    assert meta["sim_git_sha"]
    assert "recruiter_objective" not in meta
    logged = json.loads(log.read_text().splitlines()[0])
    assert logged["seed"] == 1000
    assert logged["split"] == "held_out"
    assert logged["config_sha256"] == meta["config_sha256"]
    assert logged["allow_unfrozen"] is True


def test_cli_blocks_unfrozen_heldout(tmp_path: Path, capsys):
    code = main(
        [
            "--seed",
            "1000",
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
    assert frozen["sim_code_sha256"] == sim_code_sha256()
    assert "period_size" not in frozen
    assert "access" not in frozen
