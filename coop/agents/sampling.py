"""Sampling capability for target models.

Claude Sonnet 5 rejects non-default ``temperature``, ``top_p``, and ``top_k``
(HTTP 400). Those fields are omitted. The recorded temperature is null.
``sampling`` is ``model_default``, which is not a temperature value.

Adaptive thinking is on by default for this model. ``max_tokens`` counts
thinking plus the reply, so a 512 cap can truncate the action. Low effort
is the documented control for a short JSON reply. Manual
``thinking.type=enabled`` is not sent: Sonnet 5 returns 400 for it.
"""

from __future__ import annotations

# Prefixes whose API rejects non-default sampling parameters.
_REJECTS_SAMPLING = ("claude-sonnet-5",)

SAMPLING_MODEL_DEFAULT = "model_default"
EFFORT_LOW = "low"
THINKING_ADAPTIVE = "adaptive"
# Room for a short adaptive-thinking block plus one JSON action.
SONNET5_MAX_TOKENS = 2048
LEGACY_MAX_TOKENS = 512
# Dry-run allowance. Low effort often skips thinking; this still prices a
# short thinking block because those tokens are billed as output.
LOW_EFFORT_THINKING_TOKENS = 256


def rejects_sampling(model: str) -> bool:
    """True when the Messages API rejects temperature, top_p, and top_k."""
    name = (model or "").strip().lower()
    return any(name == prefix or name.startswith(prefix + "-") for prefix in _REJECTS_SAMPLING)


def max_tokens_for(model: str) -> int:
    if rejects_sampling(model):
        return SONNET5_MAX_TOKENS
    return LEGACY_MAX_TOKENS


def sampling_label(model: str, temperature: float | None) -> str | None:
    """``model_default`` when the request omits sampling parameters."""
    if rejects_sampling(model) and temperature is None:
        return SAMPLING_MODEL_DEFAULT
    return None
