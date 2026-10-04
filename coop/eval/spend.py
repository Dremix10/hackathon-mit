"""Tally real-API spend from run ``meta.total_usd``.

Mock and scripted-only runs are excluded. A run counts when any agent model
is outside ``mock`` and ``scripted``, or when ``insider_driver`` is ``llm``.
The team cap tonight is $100, with a warning at $80.

Usage::

    python -m coop.eval.spend RUNS_DIR
    python -m coop.eval.spend RUNS_DIR --write research/spend.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

WARN_USD = 80.0
HARD_USD = 100.0
_NON_API_MODELS = frozenset({"mock", "scripted"})


@dataclass(frozen=True)
class SpendTally:
    """Dollar total and the flag the cap implies."""

    total_usd: float
    n_real_runs: int
    n_excluded_runs: int
    flag: str

    @property
    def warn(self) -> bool:
        return self.flag in {"warn", "hard"}

    @property
    def hard(self) -> bool:
        return self.flag == "hard"


def is_real_api_run(meta: dict) -> bool:
    """True when the run could have spent provider money."""
    if not isinstance(meta, dict):
        return False
    driver = meta.get("insider_driver")
    if isinstance(driver, str) and driver.strip().lower() == "llm":
        return True
    agents = meta.get("agents")
    if not isinstance(agents, list):
        return False
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        model = agent.get("model")
        if not isinstance(model, str) or not model.strip():
            continue
        if model.strip().lower() not in _NON_API_MODELS:
            return True
    return False


def _total_usd(meta: dict) -> float:
    value = meta.get("total_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def flag_for(total_usd: float) -> str:
    if total_usd >= HARD_USD:
        return "hard"
    if total_usd >= WARN_USD:
        return "warn"
    return "ok"


def tally_metas(metas: list[dict]) -> SpendTally:
    total = 0.0
    real = 0
    excluded = 0
    for meta in metas:
        if is_real_api_run(meta):
            real += 1
            total += _total_usd(meta)
        else:
            excluded += 1
    return SpendTally(
        total_usd=total,
        n_real_runs=real,
        n_excluded_runs=excluded,
        flag=flag_for(total),
    )


def load_metas(root: Path) -> list[dict]:
    """Read ``meta.json`` under ``root``. A bare file is one meta."""
    root = Path(root)
    paths: list[Path] = []
    if root.is_file() and root.name == "meta.json":
        paths = [root]
    elif root.is_dir():
        if (root / "meta.json").is_file():
            paths.append(root / "meta.json")
        paths.extend(sorted(path for path in root.rglob("meta.json") if path not in paths))
    metas: list[dict] = []
    for path in paths:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            metas.append(obj)
    return metas


def render_spend(tally: SpendTally, root: Path | None = None) -> str:
    """Markdown ledger. The cap numbers are the team rule for tonight."""
    where = str(root) if root is not None else "(no runs directory)"
    if tally.flag == "hard":
        status = f"HARD FLAG: ${tally.total_usd:.2f} is at or above the ${HARD_USD:.0f} cap."
    elif tally.flag == "warn":
        status = f"WARN: ${tally.total_usd:.2f} is at or above ${WARN_USD:.0f} and under ${HARD_USD:.0f}."
    else:
        status = f"OK: ${tally.total_usd:.2f} is under the ${WARN_USD:.0f} warning line."
    return "\n".join(
        [
            "# Real-API spend",
            "",
            "Count `meta.total_usd` only for runs that called a provider.",
            "A run is excluded when every agent model is `mock` or `scripted`",
            "and `insider_driver` is not `llm`. Mock goldens and scripted pilots",
            "do not move this total.",
            "",
            f"- Warning line: ${WARN_USD:.0f}.",
            f"- Hard flag: ${HARD_USD:.0f}. That is the team cap tonight. Stop real calls at this line.",
            "",
            "## Current tally",
            "",
            f"- Source: `{where}`.",
            f"- Real-API runs: {tally.n_real_runs}.",
            f"- Excluded mock or scripted-only runs: {tally.n_excluded_runs}.",
            f"- Total USD: {tally.total_usd:.2f}.",
            f"- Status: {status}",
            "",
            "Refresh with `python -m coop.eval.spend RUNS_DIR --write research/spend.md`.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coop.eval.spend")
    parser.add_argument("runs", nargs="?", default="runs", help="run directory to tally")
    parser.add_argument("--write", type=Path, help="overwrite this markdown file with the tally")
    args = parser.parse_args(argv)
    root = Path(args.runs)
    tally = tally_metas(load_metas(root) if root.exists() else [])
    text = render_spend(tally, root if root.exists() else None)
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")
    print(text, end="")
    if tally.hard:
        return 2
    if tally.warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
