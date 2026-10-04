"""Tests for the preregistered statistics helpers."""

import math

import pytest

from coop.eval.stats import (
    _norm_ppf,
    bootstrap_ci,
    diff_props_ci,
    fisher_exact,
    minimum_detectable_difference,
    power_budget,
    wilson_ci,
)


def test_wilson_known_values_and_boundaries():
    low, high = wilson_ci(50, 100)
    assert low == pytest.approx(0.4038315303659956)
    assert high == pytest.approx(0.5961684696340044)
    assert low < 0.5 < high
    assert wilson_ci(0, 10)[0] == 0.0
    assert wilson_ci(10, 10)[1] == 1.0
    wide_low, wide_high = wilson_ci(1, 2)
    narrow_low, narrow_high = wilson_ci(50, 100)
    assert (wide_high - wide_low) > (narrow_high - narrow_low)


def test_wilson_rejects_bad_counts():
    with pytest.raises(ValueError):
        wilson_ci(3, 2)
    with pytest.raises(ValueError):
        wilson_ci(0, 0)
    with pytest.raises(TypeError):
        wilson_ci(True, 1)


def test_bootstrap_constant_and_reproducible():
    low, high = bootstrap_ci([3, 3, 3], lambda sample: sum(sample) / len(sample), 25, 0)
    assert low == high == 3.0
    values = [1, 2, 3, 4, 8]
    fn = lambda sample: sum(sample) / len(sample)
    assert bootstrap_ci(values, fn, 300, 1) == bootstrap_ci(values, fn, 300, 1)
    assert bootstrap_ci(values, fn, 300, 1) != bootstrap_ci(values, fn, 300, 2)
    mean = sum(values) / len(values)
    lo, hi = bootstrap_ci(values, fn, 800, 0)
    assert lo < mean < hi


def test_bootstrap_rejects_empty_and_bad_fn():
    with pytest.raises(ValueError):
        bootstrap_ci([], lambda sample: 0, 10, 0)
    with pytest.raises(TypeError):
        bootstrap_ci([1, 2], lambda sample: "nope", 5, 0)


def test_diff_props_ci_contains_zero_when_equal_and_is_sharp_at_extremes():
    diff, low, high = diff_props_ci(5, 10, 5, 10, n_boot=800, seed=0)
    assert diff == 0.0
    assert low <= 0.0 <= high
    assert low < high
    diff, low, high = diff_props_ci(6, 6, 0, 6, n_boot=50, seed=1)
    assert diff == 1.0
    assert low == high == 1.0
    again = diff_props_ci(5, 10, 5, 10, n_boot=200, seed=4)
    assert again == diff_props_ci(5, 10, 5, 10, n_boot=200, seed=4)


def test_fisher_matches_published_table_and_balanced_cases():
    odds, p_value = fisher_exact([[8, 2], [1, 5]])
    assert odds == 20.0
    assert p_value == pytest.approx(0.034965034965035)
    odds, p_value = fisher_exact([[1, 1], [1, 1]])
    assert odds == 1.0
    assert p_value == pytest.approx(1.0)
    odds, p_value = fisher_exact([[6, 0], [0, 6]])
    assert math.isinf(odds)
    assert p_value == pytest.approx(0.002164502164502174)
    assert p_value < 0.05
    odds, p_value = fisher_exact([[0, 6], [0, 6]])
    assert math.isnan(odds)
    assert p_value == pytest.approx(1.0)


def test_fisher_rejects_bad_tables():
    with pytest.raises(ValueError):
        fisher_exact([[1, 2, 3], [4, 5, 6]])
    with pytest.raises(ValueError):
        fisher_exact([[0, 0], [0, 0]])
    with pytest.raises(TypeError):
        fisher_exact([[1.5, 0], [0, 1]])


def test_norm_ppf_critical_values():
    assert _norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-6)
    assert _norm_ppf(0.025) == pytest.approx(-1.959963984540054, abs=1e-6)


def test_power_budget_hackathon_envelope_and_monotonicity():
    plan = power_budget(500, 3, 3, baseline_rate=0.2)
    assert plan.n_total == 166
    assert plan.n_per_cell == 55
    assert plan.leftover_usd == pytest.approx(2.0)
    assert plan.mdd == pytest.approx(0.24085360216377316, rel=1e-6)
    assert plan.power == 0.80
    larger = power_budget(5000, 3, 3, baseline_rate=0.2)
    assert larger.mdd < plan.mdd
    sparse = power_budget(500, 3, 8, baseline_rate=0.2)
    assert sparse.n_per_cell == 20
    assert sparse.mdd > plan.mdd
    broke = power_budget(1, 3, 3)
    assert broke.n_per_cell == 0
    assert broke.mdd is None
    assert minimum_detectable_difference(1, 0.2) is None
    low = power_budget(500, 3, 3, baseline_rate=0.05)
    high = power_budget(500, 3, 3, baseline_rate=0.5)
    assert low.mdd == pytest.approx(0.17876349468246042, rel=1e-6)
    assert high.mdd == pytest.approx(0.24988466427863804, rel=1e-6)


def test_power_budget_rejects_bad_inputs():
    with pytest.raises(ValueError):
        power_budget(-1, 3, 1)
    with pytest.raises(ValueError):
        power_budget(10, 0, 1)
    with pytest.raises(ValueError):
        power_budget(10, 1, 0)
