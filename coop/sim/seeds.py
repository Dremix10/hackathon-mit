"""Held-out seed registry, use log, and the frozen behavior hash.

Discoveries count only when they replicate on held-out seeds that were not
used to tune prompts. Ranges live in ``research/seeds.json`` and are the same
file ``coop.eval.discoveries`` reads: ``dev`` is ``start`` through ``end``,
``held_out`` is ``start`` and above. Bounded draw pools sit on those objects
as ``pool``. ``python -m coop.sim.seeds freeze`` records the behavior hash.
A held-out run whose hash differs is refused unless ``--allow-unfrozen``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coop.schema import MIN_ROUNDS_CONTROLLED, ConfigError, RunConfig
from coop.sim.llm import sampling_label, sent_temperature
from coop.sim.protocol import CHARTER_TEXT, canonical_templates, target_prompts


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "research" / "seeds.json"
DEFAULT_LOG = ROOT / "research" / "seed_log.jsonl"
DEFAULT_FREEZE = ROOT / "research" / "frozen_config.json"

CANONICAL_SPLITS = ("dev", "held_out")
SPLIT_ALIASES = {
    "dev": "dev",
    "held_out": "held_out",
    "tuning": "dev",
    "heldout": "held_out",
}
# Keys of the object hashed by ``config_sha256``. Nothing else is an input.
HASH_FIELDS = (
    "target_prompts",
    "charter",
    "protocol_templates",
    "rounds_per_stage",
    "model",
    "temperature",
    "sim_code_sha256",
)


@dataclass(frozen=True)
class SeedRegistry:
    """Ranges from ``research/seeds.json``, plus the draw pools inside them."""

    dev_start: int
    dev_end: int
    held_out_start: int
    pools: dict[str, tuple[int, ...]]


def canonical_split(name: str) -> str:
    try:
        return SPLIT_ALIASES[name]
    except KeyError:
        known = ", ".join(SPLIT_ALIASES)
        raise ConfigError(f"split must be one of {known}") from None


def load_registry(path: Path | None = None) -> SeedRegistry:
    path = Path(path) if path else DEFAULT_REGISTRY
    data = json.loads(path.read_text(encoding="utf-8"))
    dev = data["dev"]
    held = data["held_out"]
    dev_start, dev_end = int(dev["start"]), int(dev["end"])
    held_start = int(held["start"])
    if dev_end < dev_start:
        raise ConfigError("dev.end is below dev.start")
    if dev_end >= held_start:
        raise ConfigError("dev and held_out ranges overlap")
    pools = {
        "dev": _as_pool(dev.get("pool", []), "dev"),
        "held_out": _as_pool(held.get("pool", []), "held_out"),
    }
    registry = SeedRegistry(dev_start, dev_end, held_start, pools)
    for split, seeds in pools.items():
        for seed in seeds:
            if split_of(seed, registry) != split:
                raise ConfigError(f"pool seed {seed} is outside {split}")
    return registry


def _as_pool(raw: Any, split: str) -> tuple[int, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"{split} pool must be a list")
    seeds = tuple(int(seed) for seed in raw)
    if len(seeds) != len(set(seeds)):
        raise ConfigError(f"duplicate seed in {split}")
    return seeds


def split_of(seed: int, registry: SeedRegistry | None = None) -> str | None:
    """``dev`` or ``held_out`` from the file ranges. Pools do not decide this."""
    registry = registry if registry is not None else load_registry()
    seed = int(seed)
    if registry.dev_start <= seed <= registry.dev_end:
        return "dev"
    if seed >= registry.held_out_start:
        return "held_out"
    return None


def resolve_seed(
    *,
    seed: int | None,
    split: str | None,
    registry: SeedRegistry,
    log_path: Path,
    redraw_failed: bool = True,
) -> tuple[int, str]:
    """Return ``(seed, split)`` with the split already canonical."""
    if split is not None:
        split = canonical_split(split)
    if seed is None and split is None:
        raise ConfigError("pass --seed N from a declared range, or --split to draw the next seed")
    if seed is None:
        assert split is not None
        return next_unused(split, registry, log_path, redraw_failed=redraw_failed), split
    found = split_of(seed, registry)
    if found is None:
        raise ConfigError(f"seed {seed} is not in a declared split")
    if split is not None and found != split:
        raise ConfigError(f"seed {seed} belongs to {found}, not {split}")
    return int(seed), found


def _logged_split(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return SPLIT_ALIASES.get(value)


FAILED_LOG_STATUSES = frozenset({"driver_failure", "error"})


def used_seeds(log_path: Path, split: str, *, redraw_failed: bool = True) -> set[int]:
    """Seeds already consumed in ``split``.

    Rows whose ``status`` is ``driver_failure`` or ``error`` are not used
    when ``redraw_failed`` is true, so a dead pilot can be drawn again.
    A row with no status is an older successful log line and stays used.
    """
    want = canonical_split(split)
    used: set[int] = set()
    if not log_path.exists():
        return used
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if _logged_split(row.get("split")) != want:
            continue
        status = row.get("status")
        if redraw_failed and status in FAILED_LOG_STATUSES:
            continue
        used.add(int(row["seed"]))
    return used


def next_unused(split: str, registry: SeedRegistry, log_path: Path, *, redraw_failed: bool = True) -> int:
    split = canonical_split(split)
    used = used_seeds(log_path, split, redraw_failed=redraw_failed)
    for seed in registry.pools[split]:
        if seed not in used:
            return seed
    raise ConfigError(f"no unused seeds left in {split}")


def take_unused(
    split: str,
    n: int,
    registry: SeedRegistry,
    log_path: Path,
    *,
    redraw_failed: bool = True,
) -> list[int]:
    split = canonical_split(split)
    used = used_seeds(log_path, split, redraw_failed=redraw_failed)
    fresh = [seed for seed in registry.pools[split] if seed not in used]
    if len(fresh) < n:
        raise ConfigError(f"{split} has {len(fresh)} unused seeds, needed {n}")
    return fresh[:n]


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sim_source_files() -> list[Path]:
    """``coop/sim/**/*.py`` and ``coop/schema.py``. Docs and tests are excluded."""
    paths = [
        path
        for path in (ROOT / "coop" / "sim").rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    paths.append(ROOT / "coop" / "schema.py")
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())


def sim_code_sha256() -> str:
    """Hash of simulator source, so a docs-only commit does not move a freeze.

    For each file in sorted repo-relative order: UTF-8 relative path, a NUL,
    the file bytes, a NUL.
    """
    digest = hashlib.sha256()
    for path in sim_source_files():
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def behavior_material(config: RunConfig) -> dict[str, Any]:
    """Exactly the inputs of ``config_sha256``.

    ``target_prompts`` is the unsubstituted ``TARGET_PROMPT`` list.
    ``charter`` is ``CHARTER_TEXT``. ``protocol_templates`` is the full
    corpus from ``canonical_templates`` (every arm, so the hash does not
    identify the selected objective). ``temperature`` is the value actually
    sent (null when the model rejects sampling params). ``sim_git_sha`` is
    not an input.
    """
    material = {
        "target_prompts": target_prompts(),
        "charter": CHARTER_TEXT,
        "protocol_templates": canonical_templates(),
        "rounds_per_stage": list(config.rounds_per_stage),
        "model": config.model,
        "temperature": sent_temperature(config.model, config.temperature),
        "sim_code_sha256": sim_code_sha256(),
    }
    if tuple(material) != HASH_FIELDS:
        raise ConfigError("behavior hash fields drifted from HASH_FIELDS")
    return material


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
    if config.seed_split != "held_out" or config.allow_unfrozen:
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
    status: str = "complete",
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "run_id": run_id,
        "seed": int(seed),
        "split": canonical_split(split),
        "config_sha256": config_digest,
        "allow_unfrozen": bool(allow_unfrozen),
        "status": status,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def write_freeze(config: RunConfig, path: Path) -> str:
    digest = config_sha256(config)
    body = {
        "config_sha256": digest,
        "model": config.model,
        "temperature": sent_temperature(config.model, config.temperature),
        "sampling": sampling_label(config.model),
        "rounds_per_stage": list(config.rounds_per_stage),
        "sim_code_sha256": sim_code_sha256(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(body) + b"\n")
    return digest


def _config_from_freeze_args(args: argparse.Namespace) -> RunConfig:
    rounds = tuple(int(part) for part in args.rounds.split(",")) or MIN_ROUNDS_CONTROLLED
    return RunConfig(
        seed=registry_seed_for_hash(),
        mode="pressure_only",
        rounds_per_stage=rounds,
        temperature=args.temperature,
        model=args.model,
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
