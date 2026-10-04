"""CLI: run one episode with the deterministic stand-in.

Example:
    uv run python -m coop.sim.run --seed 1 --condition earned_low_vis --mode controlled --objective loyalty
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from coop.schema import MIN_ROUNDS_CONTROLLED, ConfigError, RunConfig, parse_condition
from coop.sim.env import CoopSim, run_is_complete
from coop.sim.mock import PROFILES
from coop.sim.seeds import (
    DEFAULT_FREEZE,
    DEFAULT_LOG,
    DEFAULT_REGISTRY,
    append_log,
    check_heldout,
    config_sha256,
    load_registry,
    resolve_seed,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one research-cooperative episode.")
    parser.add_argument("--seed", type=int, default=None, help="Must belong to tuning or heldout in the seed registry.")
    parser.add_argument("--split", choices=["tuning", "heldout"], default=None, help="Draw the next unused seed from this pool when --seed is omitted.")
    parser.add_argument(
        "--condition",
        default="earned_low_vis",
        help="earned_low_vis | earned_high_vis | routine_low_vis | routine_high_vis",
    )
    parser.add_argument("--access", choices=["earned", "routine"], default=None)
    parser.add_argument(
        "--visibility",
        choices=["deliverable_only", "discussion_visible"],
        default=None,
    )
    parser.add_argument("--mode", choices=["spontaneous", "controlled", "pressure_only"], default="controlled")
    parser.add_argument(
        "--objective",
        choices=["resource_capture", "blame_avoidance", "loyalty", "null"],
        default=None,
        help="Controlled mode only. Stored in sealed.json, not meta.json.",
    )
    parser.add_argument("--driver", choices=["scripted", "llm"], default="scripted")
    parser.add_argument("--profile", choices=PROFILES, default="refuse_all")
    parser.add_argument("--no-conflict", action="store_true")
    parser.add_argument("--reset-on-removal", action="store_true")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--model", default="mock")
    parser.add_argument("--budget", type=float, default=3.0)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--rounds", default="4,4,5,4,4")
    parser.add_argument("--period-size", type=int, default=5)
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--allow-unfrozen", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip a run directory whose meta.status is already complete.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--seed-log", default=str(DEFAULT_LOG))
    parser.add_argument("--freeze", default=str(DEFAULT_FREEZE))
    parser.add_argument("--out", default="runs")
    return parser


def config_from_args(args: argparse.Namespace, seed: int, split: str) -> RunConfig:
    access, visibility = parse_condition(args.condition)
    if args.access:
        access = args.access
    if args.visibility:
        visibility = args.visibility
    rounds = tuple(int(part) for part in args.rounds.split(","))
    if not rounds:
        rounds = MIN_ROUNDS_CONTROLLED
    objective = args.objective
    if args.mode == "controlled" and objective is None:
        objective = "null"
    if args.mode != "controlled":
        objective = None
    return RunConfig(
        seed=seed,
        mode=args.mode,
        access=access,
        visibility=visibility,
        recruiter_objective=objective,
        insider_driver=args.driver,
        no_conflict=args.no_conflict,
        reset_on_removal=args.reset_on_removal,
        rounds_per_stage=rounds,
        temperature=args.temperature,
        model=args.model,
        budget_usd=args.budget,
        run_id=args.run_id,
        profile=args.profile,
        seed_split=split,
        allow_unfrozen=args.allow_unfrozen,
        period_size=args.period_size,
        max_actions_per_round=args.max_actions,
    )


_RUN_ID = re.compile(r"^c-(\d+)$")


def next_run_id(out: Path) -> str:
    """Next counter id under ``out``. The id never encodes the objective."""
    out.mkdir(parents=True, exist_ok=True)
    best = 0
    for child in out.iterdir():
        match = _RUN_ID.match(child.name)
        if match:
            best = max(best, int(match.group(1)))
    return f"c-{best + 1:04d}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        registry = load_registry(Path(args.registry))
        seed, split = resolve_seed(
            seed=args.seed,
            split=args.split,
            registry=registry,
            log_path=Path(args.seed_log),
        )
        config = config_from_args(args, seed, split)
        config.validate()
        check_heldout(config, Path(args.freeze))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if config.run_id is None:
        config.run_id = next_run_id(Path(args.out))
    out = Path(args.out) / config.run_id
    if args.resume and run_is_complete(out):
        print(out)
        return 0
    sim = CoopSim(config)
    sim.play()
    sim.write(out)
    append_log(
        Path(args.seed_log),
        run_id=sim.run_id,
        seed=config.seed,
        split=split,
        config_digest=config_sha256(config),
        allow_unfrozen=config.allow_unfrozen,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
