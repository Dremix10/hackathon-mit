"""Run several matched episodes and write a blinded summary.

Example:
    python -m coop.sim.batch --arms loyalty,null,pressure_only --split heldout --n 2 --backend mock

``summary.csv`` has no arm column. ``sealed_summary.csv`` does.
``--resume`` skips pairs whose sealed row already has status complete.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from coop.schema import OBJECTIVES, ConfigError, RunConfig, parse_condition, primary_outcome
from coop.sim.env import CoopSim, run_is_complete
from coop.sim.llm import AnthropicDriver
from coop.sim.mock import PROFILES
from coop.sim.run import next_run_id
from coop.sim.seeds import (
    DEFAULT_FREEZE,
    DEFAULT_LOG,
    DEFAULT_REGISTRY,
    append_log,
    check_heldout,
    config_sha256,
    load_registry,
    used_seeds,
)


PUBLIC_FIELDS = ("run_id", "seed", "seed_split", "status", "y", "usd_cost", "config_sha256")
SEALED_FIELDS = ("run_id", "arm", "seed", "seed_split", "status", "y", "usd_cost", "config_sha256")


def parse_arm(name: str) -> tuple[str, str | None]:
    if name in OBJECTIVES:
        return "controlled", name
    if name in {"pressure_only", "spontaneous"}:
        return name, None
    known = ", ".join((*OBJECTIVES, "pressure_only", "spontaneous"))
    raise ConfigError(f"unknown arm {name!r}; expected one of {known}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch research-cooperative episodes.")
    parser.add_argument("--arms", required=True, help="Comma-separated arms. The objective stays out of summary.csv.")
    parser.add_argument("--split", choices=["tuning", "heldout"], required=True)
    parser.add_argument("--n", type=int, required=True, help="How many unused seeds to draw. Each arm runs on each seed.")
    parser.add_argument("--backend", choices=["mock", "anthropic"], default="mock")
    parser.add_argument("--condition", default="earned_low_vis")
    parser.add_argument("--profile", choices=PROFILES, default="refuse_all")
    parser.add_argument("--model", default="mock")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--budget", type=float, default=3.0)
    parser.add_argument("--rounds", default="4,4,5,4,4")
    parser.add_argument("--period-size", type=int, default=5)
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--allow-unfrozen", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-conflict", action="store_true")
    parser.add_argument("--reset-on-removal", action="store_true")
    parser.add_argument("--driver", choices=["scripted", "llm"], default="scripted")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--seed-log", default=str(DEFAULT_LOG))
    parser.add_argument("--freeze", default=str(DEFAULT_FREEZE))
    parser.add_argument("--out", default="runs")
    return parser


def _read_sealed_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def _batch_seeds(
    split: str,
    n: int,
    registry: dict[str, list[int]],
    log_path: Path,
    prior_rows: list[dict[str, str]],
    resume: bool,
) -> list[int]:
    """Matched seeds. A resumed batch keeps seeds it already started."""
    started: list[int] = []
    if resume:
        for row in prior_rows:
            if row.get("seed_split") not in {None, "", split}:
                continue
            seed = int(row["seed"])
            if seed not in started:
                started.append(seed)
    if len(started) >= n:
        return started[:n]
    used = used_seeds(log_path, split)
    fresh = [seed for seed in registry[split] if seed not in started and seed not in used]
    need = n - len(started)
    if len(fresh) < need:
        raise ConfigError(f"{split} has {len(fresh)} unused seeds, needed {need}")
    return started + fresh[:need]


def _completed_pairs(rows: list[dict[str, str]]) -> set[tuple[str, int]]:
    done = set()
    for row in rows:
        if row.get("status") == "complete" and row.get("arm") and row.get("seed"):
            done.add((row["arm"], int(row["seed"])))
    return done


def _policy(args: argparse.Namespace, seed: int):
    if args.backend == "mock":
        return None
    model = args.model if args.model != "mock" else "claude-sonnet-4-5"
    return AnthropicDriver(model=model)


def run_batch(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    log_path = Path(args.seed_log)
    sealed_path_preview = Path(args.out) / "sealed_summary.csv"
    prior_preview = _read_sealed_rows(sealed_path_preview) if args.resume else []
    seeds = _batch_seeds(args.split, args.n, registry, log_path, prior_preview, args.resume)
    arms = [part.strip() for part in args.arms.split(",") if part.strip()]
    if not arms:
        raise ConfigError("pass at least one arm")
    access, visibility = parse_condition(args.condition)
    rounds = tuple(int(part) for part in args.rounds.split(","))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sealed_path = out / "sealed_summary.csv"
    prior = _read_sealed_rows(sealed_path) if args.resume else []
    done = _completed_pairs(prior) if args.resume else set()
    sealed_rows: list[dict[str, Any]] = list(prior)
    for seed in seeds:
        for arm in arms:
            if (arm, seed) in done:
                continue
            mode, objective = parse_arm(arm)
            config = RunConfig(
                seed=seed,
                mode=mode,
                access=access,
                visibility=visibility,
                recruiter_objective=objective,
                insider_driver=args.driver,
                no_conflict=args.no_conflict,
                reset_on_removal=args.reset_on_removal,
                rounds_per_stage=rounds,
                temperature=args.temperature,
                model=args.model if args.backend == "mock" else (
                    args.model if args.model != "mock" else "claude-sonnet-4-5"
                ),
                budget_usd=args.budget,
                profile=args.profile,
                seed_split=args.split,
                allow_unfrozen=args.allow_unfrozen,
                period_size=args.period_size,
                max_actions_per_round=args.max_actions,
            )
            config.validate()
            check_heldout(config, Path(args.freeze))
            run_id = next_run_id(out)
            run_dir = out / run_id
            if args.resume and run_is_complete(run_dir):
                continue
            policy = _policy(args, seed)
            sim = CoopSim(config, policy=policy)
            sim.run_id = run_id
            sim.state.run_id = run_id
            sim.play()
            sim.write(run_dir)
            digest = config_sha256(config)
            append_log(
                log_path,
                run_id=run_id,
                seed=seed,
                split=args.split,
                config_digest=digest,
                allow_unfrozen=config.allow_unfrozen,
            )
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line]
            meta = json.loads((run_dir / "meta.json").read_text())
            sealed_rows.append(
                {
                    "run_id": run_id,
                    "arm": arm,
                    "seed": seed,
                    "seed_split": args.split,
                    "status": meta.get("status"),
                    "y": primary_outcome(events),
                    "usd_cost": meta.get("total_usd"),
                    "config_sha256": digest,
                }
            )
    public_rows = [{key: row.get(key) for key in PUBLIC_FIELDS} for row in sealed_rows]
    _write_rows(out / "summary.csv", PUBLIC_FIELDS, public_rows)
    _write_rows(sealed_path, SEALED_FIELDS, sealed_rows)
    print(out / "summary.csv")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_batch(args)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
