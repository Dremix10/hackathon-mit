"""Small-sample statistics for the preregistered analysis.

Analysis imports this module. Signatures and return values:

- ``wilson_ci(k, n) -> (low, high)`` 95% Wilson interval for k successes in n trials.
- ``bootstrap_ci(values, fn, n_boot, seed) -> (low, high)`` percentile interval of
  ``fn`` on resamples of ``values``. Default level is 95%.
- ``diff_props_ci(k1, n1, k2, n2) -> (diff, low, high)`` where
  ``diff = k1/n1 - k2/n2`` and the interval is a 95% percentile bootstrap.
  Default ``n_boot=4000`` and ``seed=0`` (keyword-only).
- ``fisher_exact(table) -> (odds_ratio, p_value)`` two-sided Fisher's exact test
  on a 2x2 table ``[[a, b], [c, d]]``.

``power_budget`` is an extra planning helper. It uses a normal approximation
to a two-sided two-proportion z-test, not a simulation of Fisher power.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Acklam's inverse-normal approximation. Checked against z_0.975 in tests.
_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_P_LOW = 0.02425
_Z_95 = 1.959963984540054


def _as_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _norm_ppf(p: float) -> float:
    """Approximate standard-normal quantile (Acklam)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5])
            / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
        )
    if p > 1.0 - _P_LOW:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(
            (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5])
            / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
        )
    q = p - 0.5
    r = q * q
    return (
        (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5])
        * q
        / (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    )


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def wilson_ci(k, n):
    """95% Wilson score interval for a binomial proportion.

    Returns ``(low, high)``. ``k`` and ``n`` are ints, ``0 <= k <= n``, ``n > 0``.
    When ``k == 0`` the lower bound is 0. When ``k == n`` the upper bound is 1.
    """
    k = _as_count(k, "k")
    n = _as_count(n, "n")
    if n <= 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k} n={n}")
    z = _Z_95
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n)) / denom
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    if k == 0:
        low = 0.0
    if k == n:
        high = 1.0
    return (low, high)


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear interpolation percentile. ``pct`` is in ``[0, 100]``."""
    if not sorted_values:
        raise ValueError("no bootstrap replicates")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    x = (len(sorted_values) - 1) * (pct / 100.0)
    lo = math.floor(x)
    hi = math.ceil(x)
    if lo == hi:
        return float(sorted_values[lo])
    weight = x - lo
    return float(sorted_values[lo]) * (1.0 - weight) + float(sorted_values[hi]) * weight


def bootstrap_ci(values, fn, n_boot, seed):
    """95% percentile bootstrap interval of ``fn(sample)``.

    ``values`` is re-sampled with replacement. ``fn`` receives a list and
    returns a number. ``seed`` initializes ``random.Random``. Returns
    ``(low, high)``.
    """
    return _bootstrap_ci(values, fn, n_boot, seed, level=0.95)


def _bootstrap_ci(values, fn, n_boot, seed, level=0.95):
    n_boot = _as_count(n_boot, "n_boot")
    seed = _as_count(seed, "seed")
    if n_boot < 1:
        raise ValueError("n_boot must be >= 1")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be in (0, 1)")
    sample = list(values)
    if not sample:
        raise ValueError("values is empty")
    rng = random.Random(seed)
    n = len(sample)
    stats: list[float] = []
    for _ in range(n_boot):
        draw = [sample[rng.randrange(n)] for _ in range(n)]
        value = fn(draw)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("fn must return a real number")
        stats.append(float(value))
    stats.sort()
    alpha = (1.0 - level) / 2.0
    return (_percentile(stats, 100.0 * alpha), _percentile(stats, 100.0 * (1.0 - alpha)))


def diff_props_ci(k1, n1, k2, n2, *, n_boot=4000, seed=0, level=0.95):
    """Bootstrap interval for the difference of two independent proportions.

    Returns ``(diff, low, high)`` with ``diff = k1/n1 - k2/n2``. Each group is
    re-sampled as a binary vector. ``n_boot``, ``seed``, and ``level`` are
    keyword-only so the positional contract stays ``(k1, n1, k2, n2)``.
    """
    k1 = _as_count(k1, "k1")
    n1 = _as_count(n1, "n1")
    k2 = _as_count(k2, "k2")
    n2 = _as_count(n2, "n2")
    for k, n, name in ((k1, n1, "1"), (k2, n2, "2")):
        if n <= 0 or k < 0 or k > n:
            raise ValueError(f"need 0 <= k{name} <= n{name} and n{name} > 0")
    diff = k1 / n1 - k2 / n2
    group1 = [1] * k1 + [0] * (n1 - k1)
    group2 = [1] * k2 + [0] * (n2 - k2)
    n_boot_i = _as_count(n_boot, "n_boot")
    seed_i = _as_count(seed, "seed")
    if n_boot_i < 1:
        raise ValueError("n_boot must be >= 1")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be in (0, 1)")
    rng = random.Random(seed_i)
    stats: list[float] = []
    for _ in range(n_boot_i):
        s1 = sum(group1[rng.randrange(n1)] for _ in range(n1)) / n1
        s2 = sum(group2[rng.randrange(n2)] for _ in range(n2)) / n2
        stats.append(s1 - s2)
    stats.sort()
    alpha = (1.0 - level) / 2.0
    return (diff, _percentile(stats, 100.0 * alpha), _percentile(stats, 100.0 * (1.0 - alpha)))


def _log_comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(table):
    """Two-sided Fisher's exact test for a 2x2 table ``[[a, b], [c, d]]``.

    Returns ``(odds_ratio, p_value)``. The p-value sums the probabilities of
    tables with the same margins whose probability is at most the observed
    table's (the usual two-sided exact test). An odds ratio with a zero in
    the denominator is ``inf`` when the numerator is positive, and ``nan``
    when both products are zero.
    """
    if len(table) != 2 or any(len(row) != 2 for row in table):
        raise ValueError("table must be 2x2")
    counts = []
    for i, row in enumerate(table):
        for j, value in enumerate(row):
            try:
                counts.append(_as_count(value, f"table[{i}][{j}]"))
            except TypeError as exc:
                raise TypeError(f"table[{i}][{j}] must be an int") from exc
    a, b, c, d = counts
    if min(a, b, c, d) < 0:
        raise ValueError("table counts must be >= 0")
    n = a + b + c + d
    if n == 0:
        raise ValueError("table is empty")
    row1 = a + b
    col1 = a + c
    lo = max(0, row1 - (n - col1))
    hi = min(row1, col1)

    def log_p(x: int) -> float:
        return _log_comb(col1, x) + _log_comb(n - col1, row1 - x) - _log_comb(n, row1)

    logs = [log_p(x) for x in range(lo, hi + 1)]
    log_obs = log_p(a)
    peak = max(logs)
    weights = [math.exp(lp - peak) for lp in logs]
    total = sum(weights)
    tail = sum(w for lp, w in zip(logs, weights) if lp <= log_obs + 1e-9)
    p_value = 1.0 if total == 0 else min(1.0, tail / total)
    if b * c == 0:
        odds = float("inf") if a * d else float("nan")
    else:
        odds = (a * d) / (b * c)
    return (odds, p_value)


def _two_proportion_power(p1: float, p2: float, n: int, alpha: float) -> float:
    """Normal approximation to the power of a two-sided two-proportion z-test."""
    diff = abs(p1 - p2)
    var = p1 * (1.0 - p1) / n + p2 * (1.0 - p2) / n
    if var <= 0.0:
        return 1.0 if diff > 0.0 else 0.0
    z_crit = _norm_ppf(1.0 - alpha / 2.0)
    standardized = diff / math.sqrt(var)
    return _phi(-z_crit + standardized) + _phi(-z_crit - standardized)


def minimum_detectable_difference(
    n: int,
    baseline_rate: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> float | None:
    """Smallest increase over ``baseline_rate`` with the target power at size ``n``.

    Returns ``None`` when even a rate of 1 does not reach ``power``. This is a
    planning approximation (unpooled normal test), not the power of Fisher's exact test.
    """
    n = _as_count(n, "n")
    if n < 1:
        return None
    if not 0.0 <= baseline_rate < 1.0:
        raise ValueError("baseline_rate must be in [0, 1)")
    cap = 1.0 - baseline_rate
    if _two_proportion_power(baseline_rate, baseline_rate + cap, n, alpha) < power:
        return None
    lo = 0.0
    hi = cap
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _two_proportion_power(baseline_rate, baseline_rate + mid, n, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True)
class BudgetPlan:
    """How many runs a credit budget buys, and the difference that n can detect."""

    budget_usd: float
    cost_per_run: float
    n_cells: int
    n_total: int
    n_per_cell: int
    leftover_usd: float
    baseline_rate: float
    alpha: float
    power: float
    mdd: float | None


def power_budget(
    budget_usd: float,
    cost_per_run: float,
    n_cells: int,
    baseline_rate: float = 0.2,
    alpha: float = 0.05,
    power: float = 0.80,
) -> BudgetPlan:
    """Split a dollar budget across ``n_cells`` and report the detectable gap.

    ``n_total`` is ``floor(budget / cost)``. ``n_per_cell`` is ``n_total // n_cells``
    (leftover runs are not assigned). ``mdd`` is the minimum detectable absolute
    increase over ``baseline_rate`` for a pairwise contrast at ``power`` (default
    80%) and two-sided ``alpha``, using equal n per cell. See
    ``minimum_detectable_difference`` for the approximation.
    """
    if isinstance(budget_usd, bool) or not isinstance(budget_usd, (int, float)):
        raise TypeError("budget_usd must be a number")
    if isinstance(cost_per_run, bool) or not isinstance(cost_per_run, (int, float)):
        raise TypeError("cost_per_run must be a number")
    n_cells = _as_count(n_cells, "n_cells")
    if budget_usd < 0 or cost_per_run <= 0 or n_cells < 1:
        raise ValueError("budget_usd >= 0, cost_per_run > 0, n_cells >= 1 are required")
    if not 0.0 < alpha < 1.0 or not 0.0 < power < 1.0:
        raise ValueError("alpha and power must be in (0, 1)")
    n_total = int(math.floor(float(budget_usd) / float(cost_per_run) + 1e-9))
    n_per_cell = n_total // n_cells
    leftover = float(budget_usd) - n_total * float(cost_per_run)
    mdd = None
    if n_per_cell >= 1:
        mdd = minimum_detectable_difference(n_per_cell, baseline_rate, alpha, power)
    return BudgetPlan(
        budget_usd=float(budget_usd),
        cost_per_run=float(cost_per_run),
        n_cells=n_cells,
        n_total=n_total,
        n_per_cell=n_per_cell,
        leftover_usd=leftover,
        baseline_rate=float(baseline_rate),
        alpha=float(alpha),
        power=float(power),
        mdd=mdd,
    )
