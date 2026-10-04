"""Sweep arms × seeds. ``dry-run`` prices MockTarget traces. ``execute`` spends.

``dry-run`` runs ``CoopSim`` with ``MockTarget`` and prices each target-seat
observation with the Anthropic rate card. It does not call the API.
``execute`` spends only after ``pending_batch.json`` matches this grid, and
only with ``--human-approved`` when the estimate is over the threshold.
A successful execute appends a row to the spend log (default
``research/spend.md``) and keeps a running team total. The log warns above
$80 and ``execute`` hard-stops at $100.

Seeds 0–999 are for exploration, tuning, and pilots. Seeds 1000 and above
are held out: ``dry-run`` and ``execute`` refuse them unless
``research/record.jsonl`` has a matching ``freeze`` row. ``freeze`` writes
that row. It does not call the API.

``--batch-id`` writes each run under ``<runs-root>/<batch_id>/`` so the
directory can be copied to ``/workspace/hackathon/runs/<batch_id>/``.

The first real batch is a 10-run pilot on dev seeds. It is not confirmatory
and it is not a five-arm Latin square: one run of each recruiter objective,
three null, four pressure-only.

    python -m coop.batch dry-run --schedule pilot --batch-id pilot-001 --model claude-sonnet-5 --temperature default
    python -m coop.batch pilot-check --runs-root runs/pilot-001
    python -m coop.batch size --spend research/spend.md
    python -m coop.batch freeze --held-out-seeds pool --spend research/spend.md --frozen-config research/frozen_config.json

``size`` fills ``n_per_group`` from the pilot spend log. It refuses when
that log has no positive actual cost.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from coop.agents.budget import Budget, BudgetExceeded, global_cap_from_env
from coop.agents.detector import CONTROL_ARM, PREDICTED_DIRECTION
from coop.agents.episode import load_events, observation_calls, run_episode
from coop.agents.failures import failure_reason, mark_run_invalid
from coop.agents.fallback_analysis import write_report
from coop.agents.freeze import HELD_OUT_MIN, append_freeze, freeze_record, matching_freeze
from coop.agents.llm import AnthropicLLM, MockLLM
from coop.agents.plan import propose_candidates
from coop.agents.schedule import (
    PILOT_PAIRS,
    confirmation_seeds,
    held_out_pool,
    main_pairs,
    n_per_group,
)
from coop.agents.pricing import (
    CONTINGENCY,
    OUTPUT_TOKEN_FLOOR,
    approx_tokens,
    assumed_output_tokens,
    price_tokens,
    rate_for,
)
from coop.agents.sampling import SAMPLING_MODEL_DEFAULT, rejects_sampling
from coop.agents.prompts import find_banned, member_system, newcomer_system
from coop.schema import ACCESSES, MODES, OBJECTIVES, VISIBILITIES, RunConfig
from coop.sim.env import BudgetExceeded as SimBudgetExceeded

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TEMPERATURE = None
DEFAULT_PER_RUN_CAP_USD = 3.0
DEFAULT_RECRUITER_OBJECTIVE = "blame_avoidance"
DEFAULT_THRESHOLD_USD = 25.0
ARM_RNG = "seed^0xA2A2"
CONTRAST_ARMS = (
    "resource_capture",
    "blame_avoidance",
    "loyalty",
    "null",
    "pressure_only",
)
ARMS = CONTRAST_ARMS
LABEL_MODES = ("hidden", "shown", "swapped")
PLANTED_REQUESTS = ("resource_capture", "blame_avoidance", "loyalty")
PENDING_NAME = "research/pending_batch.json"
SPEND_NAME = "research/spend.md"
RECORD_NAME = "research/record.jsonl"
TEAM_WARN_USD = 80.0
TEAM_STOP_USD = 100.0
SPEND_HEADER = (
    "# Spend log\n\n"
    "Running team total is the sum of actual_usd. Warn above $80. Hard-stop at $100.\n\n"
    "| when | kind | runs | model | estimated_usd | actual_usd | team_total_usd | note |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
)

_ARM_CHOICES = ARMS + ("recruiter", "spontaneous", "pressure")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "dry-run":
        return cmd_dry_run(args)
    if args.cmd == "execute":
        return cmd_execute(args)
    if args.cmd == "freeze":
        return cmd_freeze(args)
    if args.cmd == "size":
        return cmd_size(args)
    if args.cmd == "pilot-check":
        return cmd_pilot_check(args)
    if args.cmd == "candidates":
        return cmd_candidates(args)
    if args.cmd == "analyze":
        return cmd_analyze(args)
    parser.error(f"unknown command {args.cmd}")
    return 2


def cmd_dry_run(args: argparse.Namespace) -> int:
    _resolve(args)
    refused = _guard_held_out(args)
    if refused:
        return refused
    grid = _grid(args)
    frozen = _guard_sim_freeze(args, grid)
    if frozen:
        return frozen
    estimate = _sweep(args, grid, backend_name="mock", dry_run=True)
    if estimate.get("api_failure"):
        return 2
    pending_path = Path(args.pending)
    pending = _pending_payload(args, grid, estimate)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps(pending, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if getattr(args, "spend", None):
        append_spend(
            Path(args.spend),
            kind="dry-run",
            runs=estimate["n_runs"],
            model=estimate["model"],
            estimated_usd=estimate["estimated_usd"],
            actual_usd=0.0,
            note=getattr(args, "spend_note", "") or "dry-run; no API call",
        )
    _print_estimate(estimate, pending_path, pending["execute_command"])
    _warn_team(Path(args.spend) if getattr(args, "spend", None) else Path(SPEND_NAME), estimate["estimated_usd"])
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    _resolve(args)
    pending_path = Path(args.pending)
    if not pending_path.exists():
        print(
            f"No {pending_path}. Run `python -m coop.batch dry-run` first.",
            file=sys.stderr,
        )
        return 2
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    grid = _grid(args)
    if pending.get("grid") != grid:
        print(
            "This command does not match the pending batch file. "
            "Dry-run this exact grid before spending.",
            file=sys.stderr,
        )
        return 2
    refused = _guard_held_out(args)
    if refused:
        return refused
    frozen = _guard_sim_freeze(args, grid)
    if frozen:
        return frozen
    spend_path = Path(getattr(args, "spend", None) or SPEND_NAME)
    stopped = _guard_team(spend_path, float(pending["estimated_usd"]))
    if stopped:
        return stopped
    threshold = _threshold(args)
    over = float(pending["estimated_usd"]) > threshold
    if over and not args.human_approved:
        print(
            f"Estimate ${pending['estimated_usd']:.2f} is over the "
            f"${threshold:.2f} approval threshold. A human has to approve "
            "the command that includes --human-approved. No API call was made.",
            file=sys.stderr,
        )
        return 3
    cap = global_cap_from_env()
    if cap is None:
        print(
            "Set COOP_BUDGET_USD before a real-API batch. No API call was made.",
            file=sys.stderr,
        )
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. No API call was made.", file=sys.stderr)
        return 2
    if float(pending["estimated_usd"]) > cap:
        print(
            f"Estimate ${pending['estimated_usd']:.2f} exceeds COOP_BUDGET_USD={cap}. "
            "No API call was made.",
            file=sys.stderr,
        )
        return 2
    backend = AnthropicLLM(model=args.model)
    try:
        estimate = _sweep(args, grid, backend_name="anthropic", dry_run=False, backend=backend)
    except (BudgetExceeded, SimBudgetExceeded) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if estimate.get("api_failure"):
        return 2
    note = getattr(args, "spend_note", "") or "execute"
    append_spend(
        spend_path,
        kind="execute",
        runs=estimate["n_runs"],
        model=estimate["model"],
        estimated_usd=float(pending["estimated_usd"]),
        actual_usd=estimate["actual_usd"],
        note=note,
    )
    _warn_team(spend_path, 0.0)
    report_root = output_root(args)
    print(
        f"Executed {estimate['n_runs']} runs, {estimate['n_calls']} calls, "
        f"actual ${estimate['actual_usd']:.4f}."
    )
    print(f"Next: python -m coop.eval.report {report_root}")
    _run_report(report_root)
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    runs_root = Path(args.runs_root)
    usd_per_run = args.usd_per_run
    if usd_per_run is None:
        usd_per_run = _pilot_usd(args.model, args.rounds)
    cap = global_cap_from_env()
    remaining = None if cap is None else max(0.0, cap - _ledger_spent(args))
    plan = propose_candidates(
        runs_root,
        usd_per_run=usd_per_run,
        budget_remaining=remaining,
    )
    text = json.dumps(plan, indent=2, sort_keys=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    summary = write_report(Path(args.runs_root), Path(args.out))
    print(json.dumps({"runs": summary.get("runs"), "out": args.out}, sort_keys=True))
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    """Record the two Fisher tests, the config hash, and the held-out seeds.

    Does not call the API. Refuses when the pilot has no measured $/run,
    so prompts, model, and temperature are not frozen on an unmeasured config.
    """
    seeds = _held_out_arg(args.held_out_seeds)
    if not seeds or any(seed < HELD_OUT_MIN for seed in seeds):
        _die(f"--held-out-seeds must be integers >= {HELD_OUT_MIN}, or 'pool'.")
    control = args.control_arm
    if control not in ("null", "pressure_only"):
        _die("--control-arm must be null or pressure_only.")
    direction = (args.predicted_direction or "").strip()
    if not direction:
        _die("--predicted-direction must be non-empty.")
    if rejects_sampling(args.model) and args.temperature is not None:
        _die(
            f"{args.model} rejects temperature, top_p, and top_k. "
            "Pass --temperature default. No API call was made."
        )
    measured = _require_measured(args)
    if measured is None:
        return 2
    from coop.eval.record import log_preregistered_tests
    from coop.schema import RunConfig
    from coop.sim.seeds import config_sha256, write_freeze

    path = Path(args.record)
    logged = log_preregistered_tests(path)
    probe = RunConfig(
        seed=1,
        mode="pressure_only",
        rounds_per_stage=_parse_rounds(getattr(args, "rounds", "4,4,5,4,4")),
        temperature=args.temperature,
        model=args.model,
    )
    digest = config_sha256(probe)
    sampling = SAMPLING_MODEL_DEFAULT if rejects_sampling(args.model) and args.temperature is None else None
    row = freeze_record(
        held_out_seeds=seeds,
        model=args.model,
        temperature=args.temperature,
        control_arm=control,
        predicted_direction=direction,
        sampling=sampling,
    )
    row["config_sha256"] = digest
    row["measured_usd_per_run"] = measured
    row["pilot_not_confirmatory"] = True
    append_freeze(path, row)
    frozen_path = getattr(args, "frozen_config", "") or ""
    if frozen_path:
        frozen = Path(frozen_path)
        write_freeze(probe, frozen)
        if sampling:
            _annotate_frozen_sampling(frozen, sampling)
    print(
        json.dumps(
            {
                "config_hash": row["config_hash"],
                "config_sha256": digest,
                "control_arm": row["control_arm"],
                "detector_id": row["detector_id"],
                "held_out_seeds": row["held_out_seeds"],
                "kind": logged["kind"],
                "measured_usd_per_run": measured,
                "record": str(path),
            },
            sort_keys=True,
        )
    )
    print(
        "Prompts, model, and temperature are fixed at this hash. "
        "Pilot runs stay non-confirmatory."
    )
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    """Print n_per_group and the main-batch command from the pilot spend log."""
    if rejects_sampling(args.model) and args.temperature is not None:
        _die(
            f"{args.model} rejects temperature, top_p, and top_k. "
            "Pass --temperature default. No API call was made."
        )
    measured = pilot_measurement(Path(args.spend))
    if measured is None:
        print(
            "n_per_group is not computable until the pilot's actual spend "
            f"is in {args.spend}. A dry-run estimate is not that measurement. "
            "No API call was made.",
            file=sys.stderr,
        )
        return 2
    pilot_spend, usd_per_run, pilot_runs = measured
    try:
        count = n_per_group(pilot_spend, usd_per_run)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    pool = held_out_pool()
    if count < 1:
        print(
            f"Budget cannot buy one seed of each group "
            f"(pilot spend ${pilot_spend:.4f}, ${usd_per_run:.4f}/run, "
            f"reserve ${10:.0f}). No API call was made.",
            file=sys.stderr,
        )
        return 2
    uncapped = count
    if count > len(pool):
        print(
            f"Formula wants n_per_group={count}, but the held-out pool has "
            f"{len(pool)} seeds. The command uses {len(pool)}.",
            file=sys.stderr,
        )
        count = len(pool)
    seeds = confirmation_seeds(count)
    if len(seeds) < count:
        print(
            f"Only {len(seeds)} held-out seeds are still unused.",
            file=sys.stderr,
        )
        count = len(seeds)
    if count < 1:
        print("No unused held-out seeds. No API call was made.", file=sys.stderr)
        return 2
    seed_text = ",".join(str(seed) for seed in seeds)
    room = round(count * 3 * usd_per_run, 2)
    command = (
        f"COOP_BUDGET_USD={room:.0f} python -m coop.batch execute --schedule main "
        f"--seeds {seed_text} --batch-id {args.batch_id} "
        f"--access earned --visibility deliverable_only --label-mode hidden "
        f"--model {args.model} --temperature {_temperature_flag(args.temperature)} "
        f"--rounds 4,4,5,4,4 --per-run-cap 3.0 --runs-root runs"
    )
    if room > DEFAULT_THRESHOLD_USD:
        command += " --human-approved"
    payload = {
        "n_per_group": count,
        "n_per_group_uncapped": uncapped,
        "pilot_spend_usd": round(pilot_spend, 4),
        "pilot_runs": pilot_runs,
        "usd_per_run": round(usd_per_run, 4),
        "seeds": seeds,
        "execute_command": command,
        "formula": "floor((100 - pilot_spend - 10) / (3 * measured_usd_per_run))",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(
        "Next freeze, if not already written: "
        f"python -m coop.batch freeze --held-out-seeds pool --model {args.model} "
        f"--temperature {_temperature_flag(args.temperature)} --spend {args.spend} "
        "--frozen-config research/frozen_config.json"
    )
    print(f"Main batch: {command}")
    print(f"After that batch: python -m coop.eval.report runs/{args.batch_id}")
    return 0


def cmd_pilot_check(args: argparse.Namespace) -> int:
    """Confirm T* and T** on real pilot output and list candidate behaviors.

    Pilot runs are never confirmatory. This command does not call Fisher
    and does not read the planted objective into its printed summary.
    """
    from coop.eval.checks import blind_manipulation_problems
    from coop.eval.detectors import DETECTORS
    from coop.eval.validate import load_runs

    root = Path(args.runs_root)
    runs = [run for run in load_runs(root) if run.meta.get("events_only") is not True]
    if not runs:
        print(f"No pilot runs under {root}. No API call was made.", file=sys.stderr)
        return 2
    problems = blind_manipulation_problems(runs)
    spent = 0.0
    real = 0
    for run in runs:
        usd = run.meta.get("total_usd")
        if isinstance(usd, (int, float)) and not isinstance(usd, bool):
            spent += float(usd)
            if float(usd) > 0:
                real += 1
    hits: dict[str, list[str]] = {}
    for run in runs:
        for name, spec in DETECTORS.items():
            found = spec.fn(run.events, run.meta)
            if found.present:
                hits.setdefault(name, []).append(run.run_id)
    print(
        json.dumps(
            {
                "confirmatory": False,
                "manipulation_ok": not problems,
                "manipulation_problems": problems,
                "n_runs": len(runs),
                "real_api_runs": real,
                "total_usd": round(spent, 4),
                "usd_per_run": round(spent / real, 4) if real else None,
                "candidate_behaviors": [
                    {
                        "name": name,
                        "confirmable": DETECTORS[name].confirmable,
                        "runs": sorted(run_ids),
                    }
                    for name, run_ids in sorted(hits.items())
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    if problems:
        print("Manipulation-check gate failed. Pilot output is not confirmatory.", file=sys.stderr)
        return 2
    if real == 0:
        print(
            "T*/T** gate ran, but total_usd is 0. That is not a measured "
            "$/run. n_per_group stays unknown.",
            file=sys.stderr,
        )
        return 2
    print("Pilot is not confirmatory. Use size after this spend is logged.")
    return 0


def _require_measured(args: argparse.Namespace) -> float | None:
    explicit = getattr(args, "measured_usd_per_run", None)
    if explicit is not None:
        if float(explicit) <= 0:
            print(
                "measured $/run must be positive. No freeze was written.",
                file=sys.stderr,
            )
            return None
        return float(explicit)
    found = pilot_measurement(Path(getattr(args, "spend", None) or SPEND_NAME))
    if found is None:
        print(
            "Freeze waits on the pilot spend log. n_per_group and the config "
            "hash are not written until actual $/run is positive. "
            "No API call was made.",
            file=sys.stderr,
        )
        return None
    return found[1]


def pilot_measurement(path: Path) -> tuple[float, float, int] | None:
    """Return (pilot_spend, usd_per_run, n_runs) from an execute row.

    Only a row whose note contains ``pilot`` and whose actual_usd is
    positive counts. Dry-run rows and a zero actual do not.
    """
    if not path.is_file():
        return None
    chosen: tuple[float, float, int] | None = None
    for row in _parse_spend_rows(path.read_text(encoding="utf-8")):
        if row.get("kind") != "execute":
            continue
        if "pilot" not in str(row.get("note") or "").lower():
            continue
        actual = float(row["actual_usd"])
        runs = int(row.get("runs") or 0)
        if actual <= 0 or runs < 1:
            continue
        chosen = (actual, actual / runs, runs)
    return chosen


def _held_out_arg(value: str) -> list[int]:
    if value.strip() == "pool":
        return held_out_pool()
    return _seeds(value)


def _run_report(runs_root: Path) -> None:
    from coop.eval.report import main as report_main

    code = report_main([str(runs_root)])
    if code:
        print(f"Report exited {code}.", file=sys.stderr)


def _parse_spend_rows(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] in {"when", "---"} or set(cells[0]) <= {"-"}:
            continue
        if len(cells) < 6:
            continue
        try:
            actual = float(cells[5])
        except ValueError:
            continue
        runs = 0
        try:
            runs = int(float(cells[2]))
        except ValueError:
            runs = 0
        note = cells[-1] if len(cells) > 6 else ""
        rows.append(
            {
                "actual_usd": actual,
                "kind": cells[1] if len(cells) > 1 else "",
                "runs": runs,
                "note": note,
            }
        )
    return rows


def team_total(path: Path) -> float:
    """Sum of actual_usd already written to the spend log."""
    if not path.is_file():
        return 0.0
    return round(sum(row["actual_usd"] for row in _parse_spend_rows(path.read_text(encoding="utf-8"))), 4)


def _guard_held_out(args: argparse.Namespace) -> int | None:
    seeds = _seeds(args.seeds)
    if not any(seed >= HELD_OUT_MIN for seed in seeds):
        return None
    record = Path(getattr(args, "record", None) or RECORD_NAME)
    match = matching_freeze(
        record,
        seeds=seeds,
        model=args.model,
        temperature=args.temperature,
    )
    if match is not None:
        return None
    print(
        f"Seeds >= {HELD_OUT_MIN} are held out. Freeze this config with "
        "`python -m coop.batch freeze` before a confirmation batch. "
        "No API call was made.",
        file=sys.stderr,
    )
    return 2


def _guard_sim_freeze(args: argparse.Namespace, grid: dict[str, Any]) -> int | None:
    """Held-out main batch must match the simulator's frozen behavior hash."""
    if grid.get("schedule") != "main":
        return None
    from coop.schema import ConfigError, RunConfig
    from coop.sim.seeds import check_heldout

    probe = RunConfig(
        seed=int(grid["seeds"][0]),
        mode="pressure_only",
        rounds_per_stage=tuple(grid["rounds"]),
        temperature=grid["temperature"],
        model=grid["model"],
        seed_split="held_out",
    )
    freeze_path = getattr(args, "frozen_config", "") or None
    try:
        check_heldout(probe, Path(freeze_path) if freeze_path else None)
    except ConfigError as exc:
        print(f"{exc} No API call was made.", file=sys.stderr)
        return 2
    return None


def _guard_team(path: Path, estimate_usd: float) -> int | None:
    """Hard-stop when actual spend, plus this estimate, would pass $100."""
    spent = team_total(path)
    if spent >= TEAM_STOP_USD or spent + estimate_usd > TEAM_STOP_USD + 1e-9:
        print(
            f"Team spend is ${spent:.2f}. This batch's estimate "
            f"${estimate_usd:.2f} would pass the ${TEAM_STOP_USD:.0f} hard stop. "
            "No API call was made.",
            file=sys.stderr,
        )
        return 2
    _warn_team(path, estimate_usd)
    return None


def _warn_team(path: Path, estimate_usd: float) -> None:
    spent = team_total(path)
    if spent >= TEAM_WARN_USD or spent + estimate_usd >= TEAM_WARN_USD:
        print(
            f"Warning: team spend is ${spent:.2f}, at or above the "
            f"${TEAM_WARN_USD:.0f} line. The hard stop is ${TEAM_STOP_USD:.0f}.",
            file=sys.stderr,
        )


def append_spend(
    path: Path,
    *,
    kind: str,
    runs: int,
    model: str,
    estimated_usd: float,
    actual_usd: float,
    note: str,
) -> float:
    """Append one markdown row and return the new running team total."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = team_total(path)
    total = round(prior + float(actual_usd), 4)
    safe_note = note.replace("|", "/").replace("\n", " ")
    row = (
        f"| {date.today().isoformat()} | {kind} | {runs} | {model} | "
        f"{estimated_usd:.4f} | {actual_usd:.4f} | {total:.4f} | {safe_note} |\n"
    )
    if not path.exists():
        path.write_text(SPEND_HEADER + row, encoding="utf-8")
        return total
    text = path.read_text(encoding="utf-8")
    if "team_total_usd" not in text:
        if not _parse_spend_rows(text):
            path.write_text(text.rstrip() + "\n\n" + SPEND_HEADER + row, encoding="utf-8")
            return total
        rebuilt = [SPEND_HEADER]
        running = 0.0
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or cells[0] in {"when", "---"} or set(cells[0]) <= {"-"}:
                continue
            if len(cells) < 6:
                continue
            try:
                actual = float(cells[5])
            except ValueError:
                continue
            running = round(running + actual, 4)
            note_cell = cells[6] if len(cells) == 7 else cells[7] if len(cells) > 7 else ""
            rebuilt.append(
                f"| {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} | {cells[4]} | "
                f"{actual:.4f} | {running:.4f} | {note_cell} |\n"
            )
        path.write_text("".join(rebuilt) + row, encoding="utf-8")
        return round(running + float(actual_usd), 4)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row)
    return total


def output_root(args: argparse.Namespace) -> Path:
    """Run directory root. ``batch_id`` is always a path component when set."""
    root = Path(args.runs_root)
    batch_id = getattr(args, "batch_id", "") or ""
    if batch_id and root.name != batch_id:
        return root / batch_id
    return root


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m coop.batch")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_grid(command: argparse.ArgumentParser, *, execute: bool) -> None:
        command.add_argument("--seeds", default="0,1,2,3,4")
        command.add_argument("--schedule", default="", choices=("", "pilot", "main"))
        command.add_argument("--access", default=None)
        command.add_argument("--visibility", default=None)
        command.add_argument("--arm", default=None)
        command.add_argument("--latin-square", action="store_true")
        command.add_argument("--mode", default=None, help=argparse.SUPPRESS)
        command.add_argument("--exploratory", action="store_true")
        command.add_argument("--label-mode", default="hidden", choices=LABEL_MODES)
        command.add_argument("--model", default=os.environ.get("COOP_MODEL", DEFAULT_MODEL))
        command.add_argument("--insider-model", default=None)
        command.add_argument("--temperature", type=temperature_arg, default="default")
        command.add_argument("--recruiter-objective", default=None)
        command.add_argument("--stage-scripts", default=None)
        command.add_argument("--run-tag", default="")
        command.add_argument("--runs-root", default="runs")
        command.add_argument("--batch-id", default="")
        command.add_argument("--record", default=RECORD_NAME)
        command.add_argument("--per-run-cap", type=float, default=DEFAULT_PER_RUN_CAP_USD)
        command.add_argument("--rounds", default="4,4,5,4,4")
        command.add_argument("--pending", default=PENDING_NAME)
        command.add_argument("--threshold", type=float, default=None)
        command.add_argument("--ledger", default=None)
        command.add_argument("--spend", default=SPEND_NAME if execute else "")
        command.add_argument("--spend-note", default="")
        command.add_argument("--frozen-config", default="")

    dry = sub.add_parser("dry-run", help="Mock sweep and a USD estimate. No API calls.")
    add_grid(dry, execute=False)
    execute = sub.add_parser("execute", help="Real Anthropic sweep. Requires a matching dry-run.")
    add_grid(execute, execute=True)
    execute.add_argument("--human-approved", action="store_true")
    candidates = sub.add_parser("candidates", help="Compare two next tests and pick one.")
    candidates.add_argument("--runs-root", default="runs")
    candidates.add_argument("--model", default=os.environ.get("COOP_MODEL", DEFAULT_MODEL))
    candidates.add_argument("--rounds", default="4,4,5,4,4")
    candidates.add_argument("--usd-per-run", type=float, default=None)
    candidates.add_argument("--ledger", default=None)
    candidates.add_argument("--out", default="")
    analyze = sub.add_parser("analyze", help="Call coop.analysis, or count violations if it is absent.")
    analyze.add_argument("--runs-root", default="runs")
    analyze.add_argument("--out", default="research/analysis.md")
    freeze = sub.add_parser("freeze", help="Record Fisher tests, config hash, and held-out seeds. No API call.")
    freeze.add_argument("--held-out-seeds", required=True)
    freeze.add_argument("--control-arm", default=CONTROL_ARM)
    freeze.add_argument("--predicted-direction", default=PREDICTED_DIRECTION)
    freeze.add_argument("--model", default=os.environ.get("COOP_MODEL", DEFAULT_MODEL))
    freeze.add_argument("--temperature", type=temperature_arg, default="default")
    freeze.add_argument("--rounds", default="4,4,5,4,4")
    freeze.add_argument("--record", default=RECORD_NAME)
    freeze.add_argument("--spend", default=SPEND_NAME)
    freeze.add_argument("--frozen-config", default="")
    freeze.add_argument("--measured-usd-per-run", type=float, default=None)
    size = sub.add_parser("size", help="Compute n_per_group from the pilot spend log. No API call.")
    size.add_argument("--spend", default=SPEND_NAME)
    size.add_argument("--pilot-runs", default="runs/pilot-001")
    size.add_argument("--model", default=os.environ.get("COOP_MODEL", DEFAULT_MODEL))
    size.add_argument("--temperature", type=temperature_arg, default="default")
    size.add_argument("--batch-id", default="main-001")
    check = sub.add_parser("pilot-check", help="T*/T** gate and candidate behaviors. Not confirmatory.")
    check.add_argument("--runs-root", default="runs/pilot-001")
    return parser


def temperature_arg(value: str) -> float | None:
    """``default`` omits sampling parameters. A number is a real temperature."""
    text = value.strip().lower()
    if text in {"default", "model_default"}:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--temperature must be a number or default"
        ) from exc


def _temperature_flag(temperature: float | None) -> str:
    if temperature is None:
        return "default"
    return str(temperature)


def _annotate_frozen_sampling(path: Path, sampling: str) -> None:
    """Record sampling beside the simulator hash. Temperature stays null."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["temperature"] = None
    data["sampling"] = sampling
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def _split(value: str, allowed: tuple[str, ...], flag: str) -> list[str]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [item for item in items if item not in allowed]
    if unknown:
        _die(f"{flag} has unknown value(s): {', '.join(unknown)}")
    return items


def _seeds(value: str) -> list[int]:
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        _die(f"--seeds must be integers, got {value!r}")
        raise exc


def _token(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def latin_arm(seed: int, cell_index: int = 0) -> str:
    stream = seed ^ 0xA2A2
    return CONTRAST_ARMS[(stream + cell_index) % len(CONTRAST_ARMS)]


def _resolve(args: argparse.Namespace) -> None:
    """Fill defaults. The stored grid does not contain the objective string."""
    if not hasattr(args, "schedule"):
        args.schedule = ""
    args.schedule = (args.schedule or "").strip()
    if args.schedule and getattr(args, "arm", None):
        _die("Pass --schedule or --arm, not both.")
    if args.schedule and getattr(args, "latin_square", False):
        _die("Pass --schedule or --latin-square, not both.")
    if args.schedule == "pilot":
        args.seeds = ",".join(str(seed) for _arm, seed in PILOT_PAIRS)
        args.arm = ""
        args.latin_square = False
        if not getattr(args, "batch_id", None):
            args.batch_id = "pilot-001"
        if not getattr(args, "spend_note", ""):
            args.spend_note = "pilot batch on dev seeds; not confirmatory"
    elif args.schedule == "main":
        args.arm = ""
        args.latin_square = False
        if not getattr(args, "batch_id", None):
            args.batch_id = "main-001"
        if not getattr(args, "spend_note", ""):
            args.spend_note = "main batch on held-out seeds"
    elif args.schedule:
        _die("--schedule must be pilot or main.")
    if rejects_sampling(getattr(args, "model", "")) and args.temperature is not None:
        _die(
            f"{args.model} rejects temperature, top_p, and top_k. "
            "Pass --temperature default. No API call was made."
        )
    if getattr(args, "arm", None) and getattr(args, "mode", None):
        _die("Pass --arm or --mode, not both.")
    if getattr(args, "mode", None) and not getattr(args, "arm", None):
        modes = _split(args.mode, MODES, "--mode")
        arms: list[str] = []
        for mode in modes:
            if mode == "pressure_only":
                arm = "pressure_only"
            elif mode == "spontaneous":
                arm = "spontaneous"
            else:
                arm = "recruiter"
            if arm not in arms:
                arms.append(arm)
        args.arm = ",".join(arms)
    if args.schedule:
        args.latin_square = False
    else:
        explicit_arm = bool(getattr(args, "arm", None))
        args.latin_square = bool(getattr(args, "latin_square", False)) or not explicit_arm
        if args.latin_square:
            args.arm = ""
        elif not getattr(args, "arm", None):
            args.arm = ",".join(ARMS)
    exploratory = bool(getattr(args, "exploratory", False))
    if not getattr(args, "access", None):
        args.access = ",".join(ACCESSES) if exploratory else "earned"
    if not getattr(args, "visibility", None):
        args.visibility = ",".join(VISIBILITIES) if exploratory else "deliverable_only"
    insider = getattr(args, "insider_model", None)
    if insider and insider != args.model and not exploratory:
        _die(
            "--insider-model differs from --model. "
            "The primary contrast uses one model. Pass --exploratory for a different insider model."
        )
    if not insider:
        args.insider_model = args.model
    objective = getattr(args, "recruiter_objective", None) or os.environ.get(
        "COOP_RECRUITER_OBJECTIVE", DEFAULT_RECRUITER_OBJECTIVE
    )
    if objective not in PLANTED_REQUESTS and objective != "null":
        _die(
            "--recruiter-objective must be one of: "
            + ", ".join(PLANTED_REQUESTS)
            + ", null."
        )
    args.recruiter_objective = objective
    args.stage_scripts_data = _load_scripts(getattr(args, "stage_scripts", None))
    if not hasattr(args, "run_tag") or args.run_tag is None:
        args.run_tag = ""
    if not hasattr(args, "temperature"):
        args.temperature = DEFAULT_TEMPERATURE
    if not hasattr(args, "spend"):
        args.spend = ""
    if not hasattr(args, "spend_note"):
        args.spend_note = ""
    if not getattr(args, "batch_id", None):
        args.batch_id = ""
    if not getattr(args, "record", None):
        args.record = RECORD_NAME


def _load_scripts(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.is_file():
        _die(f"--stage-scripts file not found: {path}")
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"--stage-scripts is not JSON: {exc}")
    if not isinstance(raw, dict):
        _die("--stage-scripts must be a JSON object keyed by stage 1-5.")
    scripts: dict[str, str] = {}
    for key, text in raw.items():
        stage = str(key)
        if stage not in {"1", "2", "3", "4", "5"}:
            _die(f"--stage-scripts key must be 1-5, got {key!r}")
        if not isinstance(text, str) or not text.strip():
            _die(f"--stage-scripts[{key!r}] must be a non-empty string.")
        found = find_banned(text)
        if found:
            _die(f"--stage-scripts[{key!r}] contains banned wording.")
        scripts[stage] = text
    return scripts


def _grid(args: argparse.Namespace) -> dict[str, Any]:
    scripts = getattr(args, "stage_scripts_data", {})
    latin = bool(getattr(args, "latin_square", False))
    body: dict[str, Any] = {
        "access": _split(args.access, ACCESSES, "--access"),
        "arm_rng": ARM_RNG,
        "batch_id": getattr(args, "batch_id", "") or "",
        "cell_index": 0,
        "exploratory": bool(getattr(args, "exploratory", False)),
        "insider_driver": "scripted",
        "insider_model": args.insider_model,
        "label_mode": args.label_mode,
        "latin_square": latin,
        "model": args.model,
        "schedule": getattr(args, "schedule", "") or "",
        "per_run_cap": args.per_run_cap,
        "rounds": list(_parse_rounds(args.rounds)),
        "run_tag": args.run_tag or "",
        "sampling": SAMPLING_MODEL_DEFAULT if rejects_sampling(args.model) and args.temperature is None else None,
        "seeds": _seeds(args.seeds),
        "stage_scripts_token": _token(json.dumps(scripts, sort_keys=True, separators=(",", ":"))),
        "temperature": args.temperature,
        "visibility": _split(args.visibility, VISIBILITIES, "--visibility"),
    }
    if getattr(args, "schedule", "") == "pilot":
        body["pairs"] = [[arm, seed] for arm, seed in PILOT_PAIRS]
        return body
    if getattr(args, "schedule", "") == "main":
        try:
            pairs = main_pairs(_seeds(args.seeds))
        except ValueError as exc:
            _die(str(exc))
        body["pairs"] = [[arm, seed] for arm, seed in pairs]
        return body
    if latin:
        return body
    body["arm"] = _split(args.arm, _ARM_CHOICES, "--arm")
    if "recruiter" in body["arm"]:
        body["recruiter_objective_token"] = _token(args.recruiter_objective)
    return body


def _mode_objective(arm: str, objective: str) -> tuple[str, str | None]:
    if arm in ("pressure_only", "pressure"):
        return "pressure_only", None
    if arm == "spontaneous":
        return "spontaneous", None
    if arm == "null":
        return "controlled", "null"
    if arm in PLANTED_REQUESTS:
        return "controlled", arm
    if arm == "recruiter":
        if objective == "null":
            return "controlled", "null"
        return "controlled", objective
    _die(f"unknown arm {arm!r}")
    return "controlled", objective


def _configs(args: argparse.Namespace, grid: dict[str, Any]) -> list[RunConfig]:
    configs = []
    rounds = tuple(grid["rounds"])
    pairs: list[tuple[str, int]] = []
    if grid.get("pairs"):
        for arm, seed in grid["pairs"]:
            pairs.append((str(arm), int(seed)))
    elif grid.get("latin_square"):
        for seed in grid["seeds"]:
            pairs.append((latin_arm(seed, grid.get("cell_index", 0)), seed))
    else:
        for arm in grid["arm"]:
            for seed in grid["seeds"]:
                pairs.append((arm, seed))
    index = 0
    for access in grid["access"]:
        for visibility in grid["visibility"]:
            for arm, seed in pairs:
                index += 1
                mode, objective = _mode_objective(arm, args.recruiter_objective)
                split = None
                if grid.get("schedule") == "pilot":
                    split = "dev"
                elif grid.get("schedule") == "main":
                    split = "held_out"
                configs.append(
                    RunConfig(
                        seed=seed,
                        mode=mode,
                        access=access,
                        visibility=visibility,
                        recruiter_objective=objective,
                        insider_driver="scripted",
                        rounds_per_stage=rounds,
                        temperature=grid["temperature"],
                        model=grid["model"],
                        budget_usd=float(grid["per_run_cap"]),
                        run_id=f"c-{seed:04d}-{index:02d}",
                        profile="refuse_all",
                        seed_split=split,
                    )
                )
    return configs


def _parse_rounds(value: Any) -> tuple[int, ...]:
    if isinstance(value, int):
        return (value,) * 5
    if isinstance(value, (list, tuple)):
        rounds = tuple(int(item) for item in value)
    else:
        text = str(value)
        if "," in text:
            rounds = tuple(int(part.strip()) for part in text.split(",") if part.strip())
        else:
            rounds = (int(text),) * 5
    if len(rounds) != 5:
        _die(f"--rounds needs 5 integers, got {value!r}")
    return rounds


def _ledger_path(args: argparse.Namespace, runs_root: Path) -> Path:
    if getattr(args, "ledger", None):
        return Path(args.ledger)
    env = os.environ.get("COOP_BUDGET_LEDGER")
    if env:
        return Path(env)
    return runs_root / "budget.json"


def _sweep(
    args: argparse.Namespace,
    grid: dict[str, Any],
    *,
    backend_name: str,
    dry_run: bool,
    backend: Any = None,
) -> dict[str, Any]:
    rate_for(grid["model"])
    if grid["insider_model"] != grid["model"]:
        rate_for(grid["insider_model"])
    runs_root = output_root(args)
    budget = Budget(
        per_run_cap_usd=args.per_run_cap,
        global_cap_usd=global_cap_from_env(),
        ledger_path=_ledger_path(args, runs_root),
    )
    if backend is None:
        backend = MockLLM(model="mock")
    member_tokens = approx_tokens(member_system())
    newcomer_tokens = approx_tokens(newcomer_system())
    n_calls = 0
    tokens_in = 0
    tokens_out_assumed = 0
    actual_usd = 0.0
    priced_usd = 0.0
    aborted: list[str] = []
    configs = _configs(args, grid)
    for config in configs:
        try:
            meta = run_episode(
                config,
                backend,
                budget,
                runs_root,
                dry_run=dry_run,
                backend_name=backend_name,
                batch_id=getattr(args, "batch_id", "") or "",
            )
        except (BudgetExceeded, SimBudgetExceeded) as exc:
            aborted.append(config.run_id or "")
            if getattr(exc, "scope", "") == "global":
                break
            continue
        if meta.get("aborted"):
            aborted.append(config.run_id or "")
            reason = meta.get("abort_reason") or ""
            if isinstance(reason, str) and reason.startswith("global"):
                break
        events = load_events(runs_root / config.run_id)
        api_reason = failure_reason(events, meta)
        if api_reason:
            mark_run_invalid(runs_root / config.run_id, meta, api_reason)
            message = (
                f"{config.run_id}: {api_reason}. "
                "Stopping the batch. This run is marked invalid in meta.json. "
                "No further runs were started."
            )
            print(message, file=sys.stderr)
            return {
                "n_runs": len(configs),
                "n_calls": n_calls,
                "tokens_in": tokens_in,
                "tokens_out_assumed": tokens_out_assumed,
                "actual_usd": round(actual_usd, 6),
                "estimated_usd": 0.0,
                "priced_usd_before_contingency": round(priced_usd, 4),
                "aborted": aborted,
                "model": grid["model"],
                "output_token_floor": assumed_output_tokens(grid["model"]),
                "contingency": CONTINGENCY,
                "api_failure": message,
            }
        if dry_run:
            skip = {"a0"} if config.mode == "controlled" else set()
            for event in observation_calls(events, skip_actors=skip):
                actor = event.get("actor") or ""
                rendered = str((event.get("payload") or {}).get("rendered") or "")
                system_tokens = newcomer_tokens if actor == "a4" else member_tokens
                call_in = system_tokens + approx_tokens(rendered)
                assumed_out = assumed_output_tokens(grid["model"])
                n_calls += 1
                tokens_in += call_in
                tokens_out_assumed += assumed_out
                priced_usd += price_tokens(grid["model"], call_in, assumed_out)
        else:
            for event in events:
                if event.get("type") != "llm_call":
                    continue
                payload = event["payload"]
                n_calls += 1
                tokens_in += int(payload["tokens_in"])
                assumed_out = max(int(payload["tokens_out"]), assumed_output_tokens(grid["model"]))
                tokens_out_assumed += assumed_out
                actual_usd += float(payload["usd_cost"])
                priced_usd += price_tokens(grid["model"], int(payload["tokens_in"]), assumed_out)
    estimated = round(priced_usd * CONTINGENCY, 4)
    return {
        "n_runs": len(configs),
        "n_calls": n_calls,
        "tokens_in": tokens_in,
        "tokens_out_assumed": tokens_out_assumed,
        "actual_usd": round(actual_usd, 6),
        "estimated_usd": estimated,
        "priced_usd_before_contingency": round(priced_usd, 4),
        "aborted": aborted,
        "model": grid["model"],
        "output_token_floor": assumed_output_tokens(grid["model"]),
        "contingency": CONTINGENCY,
    }


def _threshold(args: argparse.Namespace) -> float:
    if args.threshold is not None:
        return float(args.threshold)
    raw = os.environ.get("COOP_APPROVAL_THRESHOLD_USD")
    if raw:
        return float(raw)
    return DEFAULT_THRESHOLD_USD


def _execute_command(args: argparse.Namespace, over: bool) -> str:
    parts = ["python -m coop.batch execute"]
    if getattr(args, "schedule", ""):
        parts.append(f"--schedule {args.schedule}")
        if args.schedule == "main":
            parts.append(f"--seeds {args.seeds}")
    else:
        parts.append(f"--seeds {args.seeds}")
    parts.extend(
        [
            f"--access {args.access}",
            f"--visibility {args.visibility}",
        ]
    )
    if not getattr(args, "schedule", ""):
        if getattr(args, "latin_square", False):
            parts.append("--latin-square")
        else:
            parts.append(f"--arm {args.arm}")
    parts.extend(
        [
            f"--label-mode {args.label_mode}",
            f"--model {args.model}",
            f"--temperature {_temperature_flag(args.temperature)}",
            f"--rounds {','.join(str(item) for item in _parse_rounds(args.rounds))}",
            f"--per-run-cap {args.per_run_cap}",
            f"--runs-root {args.runs_root}",
        ]
    )
    if getattr(args, "batch_id", ""):
        parts.append(f"--batch-id {args.batch_id}")
    if args.exploratory:
        parts.append("--exploratory")
    if args.insider_model and args.insider_model != args.model:
        parts.append(f"--insider-model {args.insider_model}")
    if args.stage_scripts:
        parts.append(f"--stage-scripts {args.stage_scripts}")
    if args.run_tag:
        parts.append(f"--run-tag {args.run_tag}")
    if over:
        parts.append("--human-approved")
    return " ".join(parts)


def _pending_payload(
    args: argparse.Namespace,
    grid: dict[str, Any],
    estimate: dict[str, Any],
) -> dict[str, Any]:
    threshold = _threshold(args)
    over = estimate["estimated_usd"] > threshold
    return {
        "grid": grid,
        "estimated_usd": estimate["estimated_usd"],
        "priced_usd_before_contingency": estimate["priced_usd_before_contingency"],
        "n_runs": estimate["n_runs"],
        "n_calls": estimate["n_calls"],
        "tokens_in": estimate["tokens_in"],
        "tokens_out_assumed": estimate["tokens_out_assumed"],
        "approval_threshold_usd": threshold,
        "over_threshold": over,
        "output_token_floor": estimate.get("output_token_floor", OUTPUT_TOKEN_FLOOR),
        "contingency": CONTINGENCY,
        "execute_command": _execute_command(args, over),
        "note": (
            "Dry-run used MockTarget and made no API calls. "
            "estimated_usd prices rendered observations at the rate card, "
            f"assumes at least {estimate.get('output_token_floor', OUTPUT_TOKEN_FLOOR)} output tokens per call, "
            f"and adds a {CONTINGENCY} contingency. "
            + (
                "Pilot schedule: 3 recruiter (one objective each), 3 null, "
                "4 pressure_only. These runs are not confirmatory. "
                "The dry-run estimate is not the measured $/run."
                if grid.get("schedule") == "pilot"
                else "Main schedule: equal recruiter, null, and pressure_only groups. Not a five-arm Latin square."
                if grid.get("schedule") == "main"
                else "The planted request is not in this file."
            )
        ),
    }


def _print_estimate(estimate: dict[str, Any], pending_path: Path, command: str) -> None:
    print(
        f"Dry-run: {estimate['n_runs']} runs, {estimate['n_calls']} calls, "
        f"{estimate['tokens_in']} input tokens, "
        f"{estimate['tokens_out_assumed']} assumed output tokens."
    )
    print(
        f"Estimate ${estimate['estimated_usd']:.2f} "
        f"(${estimate['priced_usd_before_contingency']:.2f} before {CONTINGENCY}x contingency) "
        f"for {estimate['model']}."
    )
    print(f"Wrote {pending_path}")
    print(f"Next: {command}")


def _pilot_usd(model: str, rounds: Any) -> float:
    """Price one Latin-square seed and return that contingent estimate."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        namespace = argparse.Namespace(
            cmd="dry-run",
            seeds="0",
            access="earned",
            visibility="deliverable_only",
            arm=None,
            latin_square=True,
            mode=None,
            exploratory=False,
            label_mode="hidden",
            model=model,
            insider_model=None,
            temperature=None,
            recruiter_objective=None,
            stage_scripts=None,
            run_tag="",
            runs_root=str(root / "runs"),
            per_run_cap=DEFAULT_PER_RUN_CAP_USD,
            rounds=rounds,
            pending=str(root / "pending.json"),
            threshold=DEFAULT_THRESHOLD_USD,
            ledger=str(root / "budget.json"),
            human_approved=False,
            spend="",
            spend_note="",
            batch_id="",
            record=str(root / "record.jsonl"),
        )
        _resolve(namespace)
        grid = _grid(namespace)
        estimate = _sweep(namespace, grid, backend_name="mock", dry_run=True)
    return estimate["estimated_usd"]


def _ledger_spent(args: argparse.Namespace) -> float:
    path = _ledger_path(args, Path(args.runs_root))
    if not path.exists():
        return 0.0
    return float(json.loads(path.read_text(encoding="utf-8")).get("spent_usd", 0.0))


if __name__ == "__main__":
    raise SystemExit(main())
