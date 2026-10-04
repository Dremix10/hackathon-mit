"""Per-run and global USD caps.

The global cap is ``COOP_BUDGET_USD``. When that variable is unset there is
no global cap, and a real-API batch refuses to start. Mock runs charge zero
and do not need the variable. The ledger file accumulates spend across runs.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


class BudgetExceeded(RuntimeError):
    def __init__(self, scope: str, spent: float, cap: float):
        self.scope = scope
        self.spent = spent
        self.cap = cap
        super().__init__(
            f"{scope} budget exceeded: spent ${spent:.4f} of cap ${cap:.4f}"
        )


def global_cap_from_env() -> float | None:
    raw = os.environ.get("COOP_BUDGET_USD")
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


class Budget:
    def __init__(
        self,
        per_run_cap_usd: float,
        global_cap_usd: float | None,
        ledger_path: Path,
    ):
        self.per_run_cap_usd = per_run_cap_usd
        self.global_cap_usd = global_cap_usd
        self.ledger_path = ledger_path
        self.run_spent = 0.0

    def reset_run(self) -> None:
        """Start a new episode. The global ledger is unchanged."""
        self.run_spent = 0.0

    def _lock_path(self) -> Path:
        return self.ledger_path.with_suffix(self.ledger_path.suffix + ".lock")

    def _read_global(self) -> float:
        if not self.ledger_path.exists():
            return 0.0
        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        return float(data.get("spent_usd", 0.0))

    def _write_global(self, spent: float) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps({"spent_usd": round(spent, 8)}) + "\n",
            encoding="utf-8",
        )

    def _update(self, delta: float, *, record_overage: bool) -> None:
        """Add ``delta`` under the ledger lock.

        A reservation refuses before the numbers change. A settlement records
        the actual spend and then raises, so the call that crossed the cap
        is still in the ledger.
        """
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                new_run = self.run_spent + delta
                current = self._read_global()
                new_global = current + delta
                per_run_over = new_run - self.per_run_cap_usd > 1e-9
                global_over = (
                    self.global_cap_usd is not None
                    and new_global - self.global_cap_usd > 1e-9
                )
                if (per_run_over or global_over) and not record_overage:
                    if per_run_over:
                        raise BudgetExceeded("per-run", new_run, self.per_run_cap_usd)
                    raise BudgetExceeded("global", new_global, self.global_cap_usd)
                self.run_spent = new_run
                self._write_global(new_global)
                if per_run_over:
                    raise BudgetExceeded("per-run", new_run, self.per_run_cap_usd)
                if global_over:
                    raise BudgetExceeded("global", new_global, self.global_cap_usd)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def reserve(self, estimate_usd: float) -> None:
        """Hold the worst-case price before a call. Refund via ``settle``."""
        if estimate_usd < 0:
            raise ValueError("estimate_usd must be >= 0")
        if estimate_usd == 0:
            return
        self._update(estimate_usd, record_overage=False)

    def settle(self, estimate_usd: float, actual_usd: float) -> None:
        """Replace a reservation with the actual charge."""
        delta = actual_usd - estimate_usd
        if delta == 0:
            return
        self._update(delta, record_overage=True)

    def global_spent(self) -> float:
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._read_global()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
