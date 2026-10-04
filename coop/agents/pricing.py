"""Anthropic list prices used for budgets and dry-run estimates.

Rates are USD per million tokens, base input and output, from
https://platform.claude.com/docs/en/about-claude/pricing as of 2026-10-04.
Cache writes and batch discounts are not assumed. Unknown model ids are
refused so an estimate cannot silently undercharge.
"""

from __future__ import annotations

from dataclasses import dataclass

# Longest-prefix match. More specific ids must come first.
_RATES: tuple[tuple[str, float, float], ...] = (
    ("claude-haiku-4-5", 1.0, 5.0),
    ("claude-haiku-3-5", 0.80, 4.0),
    ("claude-sonnet-5-5", 2.0, 10.0),
    ("claude-sonnet-5", 2.0, 10.0),
    ("claude-sonnet-4-6", 3.0, 15.0),
    ("claude-sonnet-4-5", 3.0, 15.0),
    ("claude-opus-5-5", 4.0, 20.0),
    ("claude-opus-5", 5.0, 25.0),
    ("claude-opus-4-8", 5.0, 25.0),
    ("claude-opus-4-7", 5.0, 25.0),
    ("claude-opus-4-6", 5.0, 25.0),
    ("claude-fable-5", 10.0, 50.0),
)

# Real replies are longer than the mock JSON. The dry-run prices at least
# this many output tokens per call so the estimate is not a lower bound.
OUTPUT_TOKEN_FLOOR = 350
# Extra margin reported alongside the raw estimate.
CONTINGENCY = 1.25


class UnknownModelError(ValueError):
    pass


@dataclass(frozen=True)
class Rate:
    model_prefix: str
    usd_per_mtok_in: float
    usd_per_mtok_out: float


def rate_for(model: str) -> Rate:
    for prefix, usd_in, usd_out in _RATES:
        if model == prefix or model.startswith(prefix + "-"):
            return Rate(prefix, usd_in, usd_out)
    known = ", ".join(prefix for prefix, _, _ in _RATES)
    raise UnknownModelError(
        f"No price for model {model!r}. Known prefixes: {known}. "
        "Set --model to one of those ids (dated snapshots such as "
        "claude-sonnet-5-20260101 match by prefix)."
    )


def price_tokens(model: str, tokens_in: int, tokens_out: int) -> float:
    rate = rate_for(model)
    return (tokens_in * rate.usd_per_mtok_in + tokens_out * rate.usd_per_mtok_out) / 1_000_000


def approx_tokens(text: str) -> int:
    """Character/4 estimate. The live API replaces this with usage counts."""
    return max(1, (len(text) + 3) // 4)


def assumed_output_tokens(model: str) -> int:
    """Output tokens priced per call, including low-effort thinking when billed.

    Sonnet 5 bills adaptive thinking as output. ``effort=low`` often skips it.
    The estimate still adds that allowance so a 350-token reply floor is not
    the bill for a thinking model. The API ``max_tokens`` cap is larger; this
    is the expected charge, not the cap.
    """
    from coop.agents.sampling import LOW_EFFORT_THINKING_TOKENS, rejects_sampling

    if rejects_sampling(model):
        return OUTPUT_TOKEN_FLOOR + LOW_EFFORT_THINKING_TOKENS
    return OUTPUT_TOKEN_FLOOR
