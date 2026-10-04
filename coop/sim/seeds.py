"""Held-out seed registry, use log, and the frozen behavior hash.

Discoveries count only when they replicate on held-out seeds that were not
used to tune prompts. ``python -m coop.sim.seeds freeze`` records the hash of
the behavior config. A held-out run whose hash differs is refused unless
``--allow-unfrozen`` is set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coop.schema import MIN_ROUNDS_CONTROLLED, ConfigError, RunConfig
from coop.sim.protocol import PROTOCOL_VERSION, canonical_templates


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "research" / "seeds.json"
DEFAULT_LOG = ROOT / "research" / "seed_log.jsonl"
DEFAULT_FREEZE = ROOT / "research" / "frozen_config.json"

SPLITS = ("tuning", "heldout")


def load_registry(path: Path | None = None) -> dict[str, list[int]]:
    path = Path(path) if path else DEFAULT_REGISTRY
    data = json.loads(path.read_text(encoding="utf-8"))
    registry = {split: [int(seed) for seed in data[split]] for split in SPLITS}
    tuning, heldout = set(registry["tuning"]), set(registry["heldout"])
    if tuning & heldout:
        raise ConfigError("tuning and heldout seed pools overlap")
    for split, seeds in registry.items():
        if len(seeds) != len(set(seeds)):
            raise ConfigError(f"duplicate seed in {split}")
    return registry


def split_of(seed: int, registry: dict[str, list[int]] | None = None) -> str | None:
    registry = registry if registry is not None else load_registry()
    for split in SPLITS:
        if int(seed) in registry[split]:
            return split
    return None


def resolve_seed(
    *,
    seed: int | None,
    split: str | None,
    registry: dict[str, list[int]],
    log_path: Path,
) -> tuple[int, str]:
    """Return ``(seed, split)``. A bare seed must sit in exactly one pool."""
    if split is not None and split not in SPLITS:
        raise ConfigError(f"split must be one of {SPLITS}")
    if seed is None and split is None:
        raise ConfigError("pass --seed N from a declared pool, or --split to draw the next seed")
    if seed is None:
        assert split is not None
        return next_unused(split, registry, log_path), split
    found = split_of(seed, registry)
    if found is None:
        raise ConfigError(f"seed {seed} is not in a declared split")
    if split is not None and found != split:
        raise ConfigError(f"seed {seed} belongs to {found}, not {split}")
    return int(seed), found


def used_seeds(log_path: Path, split: str) -> set[int]:
    used: set[int] = set()
    if not log_path.exists():
        return used
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("split") == split:
            used.add(int(row["seed"]))
    return used


def next_unused(split: str, registry: dict[str, list[int]], log_path: Path) -> int:
    used = used_seeds(log_path, split)
    for seed in registry[split]:
        if seed not in used:
            return seed
    raise ConfigError(f"no unused seeds left in {split}")


def take_unused(split: str, n: int, registry: dict[str, list[int]], log_path: Path) -> list[int]:
    used = used_seeds(log_path, split)
    fresh = [seed for seed in registry[split] if seed not in used]
    if len(fresh) < n:
        raise ConfigError(f"{split} has {len(fresh)} unused seeds, needed {n}")
    return fresh[:n]


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def behavior_material(config: RunConfig) -> dict[str, Any]:
    """Settings that change what a target agent sees or how far it may act.

    The objective is not included. Every arm hashes the same template corpus.
    ``TARGET_PROMPT`` is inside that corpus.
    """
    return {
        "protocol_version": PROTOCOL_VERSION,
        "templates": canonical_templates(),
        "rounds_per_stage": list(config.rounds_per_stage),
        "model": config.model,
        "temperature": config.temperature,
        "period_size": config.period_size,
        "max_actions_per_round": config.max_actions_per_round,
        "access": config.access,
        "visibility": config.visibility,
    }


def config_sha256(config: RunConfig) -> str:
    return hashlib.sha256(_canonical(behavior_material(config))).hexdigest()


def read_frozen(path: Path | None = None) -> str | None:
    path = Path(path) if path else DEFAULT_FREEZE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("config_sha256")
    return str(value) if value else None


def check_heldout(config: RunConfig, freeze_path: Path | None = None) -> None:
    """Refuse a held-out run whose behavior hash is not the frozen one."""
    if config.seed_split != "heldout" or config.allow_unfrozen:
        return
    frozen = read_frozen(freeze_path)
    current = config_sha256(config)
    if frozen is None:
        raise ConfigError(
            "held-out seed has no frozen config; run python -m coop.sim.seeds freeze "
            "or pass --allow-unfrozen"
        )
    if frozen != current:
        raise ConfigError(
            "held-out config_sha256 does not match the frozen config; "
            "pass --allow-unfrozen to run it anyway"
        )


def append_log(
    log_path: Path,
    *,
    run_id: str,
    seed: int,
    split: str,
    config_digest: str,
    allow_unfrozen: bool = False,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "run_id": run_id,
        "seed": int(seed),
        "split": split,
        "config_sha256": config_digest,
        "allow_unfrozen": bool(allow_unfrozen),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def write_freeze(config: RunConfig, path: Path) -> str:
    digest = config_sha256(config)
    body = {
        "config_sha256": digest,
        "protocol_version": PROTOCOL_VERSION,
        "model": config.model,
        "temperature": config.temperature,
        "rounds_per_stage": list(config.rounds_per_stage),
        "period_size": config.period_size,
        "max_actions_per_round": config.max_actions_per_round,
        "access": config.access,
        "visibility": config.visibility,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(body) + b"\n")
    return digest


def _config_from_freeze_args(args: argparse.Namespace) -> RunConfig:
    rounds = tuple(int(part) for part in args.rounds.split(",")) or MIN_ROUNDS_CONTROLLED
    return RunConfig(
        seed=registry_seed_for_hash(),
        mode="pressure_only",
        access=args.access,
        visibility=args.visibility,
        rounds_per_stage=rounds,
        temperature=args.temperature,
        model=args.model,
        period_size=args.period_size,
        max_actions_per_round=args.max_actions,
    )


def registry_seed_for_hash() -> int:
    """Any declared seed. The hash does not depend on it."""
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed registry and frozen behavior hash.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    freeze = sub.add_parser("freeze", help="Write research/frozen_config.json for the current behavior config.")
    freeze.add_argument("--model", default="mock")
    freeze.add_argument("--temperature", type=float, default=None)
    freeze.add_argument("--rounds", default="4,4,5,4,4")
    freeze.add_argument("--period-size", type=int, default=5)
    freeze.add_argument("--max-actions", type=int, default=8)
    freeze.add_argument("--access", choices=["earned", "routine"], default="earned")
    freeze.add_argument("--visibility", choices=["deliverable_only", "discussion_visible"], default="deliverable_only")
    freeze.add_argument("--out", default=str(DEFAULT_FREEZE))
    args = parser.parse_args(argv)
    if args.cmd == "freeze":
        try:
            config = _config_from_freeze_args(args)
            config.validate()
        except ConfigError as exc:
            print(str(exc))
            return 2
        digest = write_freeze(config, Path(args.out))
        print(digest)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
