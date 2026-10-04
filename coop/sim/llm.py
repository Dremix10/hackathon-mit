"""Anthropic target driver.

The simulator does not price calls itself. This driver estimates ``usd_cost``
from ``PRICE_PER_MILLION`` and reports it through ``sim.record_llm_call``.
A wrapper that knows the invoice cost should call ``record_llm_call`` with
that figure instead. Either way the $3 cap and ``COOP_BUDGET_USD`` apply.

``MockTarget.immediate`` is the only same-round vote reaction. An LLM driver
that needs the loyalty vote to land before the next scripted step must
implement ``immediate(step, state)`` itself.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from coop.schema import Action
from coop.sim.protocol import TARGET_PROMPT, render


# USD per million tokens (input, output). Edit this table to match the bill.
# Unknown model names fall back to the sonnet row so a missing entry still
# spends against the cap instead of looking free.
PRICE_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-opus-4-1": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_FALLBACK_PRICE = PRICE_PER_MILLION["claude-sonnet-4-5"]


def estimate_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICE_PER_MILLION.get(model, _FALLBACK_PRICE)
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def parse_actions(text: str) -> list[dict[str, Any]]:
    """Pull an action dict or a list of them out of a model reply.

    Raises ``ValueError`` when the reply is not JSON. The simulator turns
    that into ``action_rejected`` if the caller returns the raw text instead.
    """
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    parsed = json.loads(body)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("action JSON must be an object or a list")


class AnthropicDriver:
    """One target policy. Call ``bind(sim)`` before ``act`` (the sim does)."""

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 400) -> None:
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_tokens = max_tokens
        self.sim: Any = None

    def bind(self, sim: Any) -> None:
        self.sim = sim

    def act(self, agent_id: str, observation: dict[str, Any]) -> list[Any]:
        if not self.api_key:
            return ["ANTHROPIC_API_KEY is not set"]
        system = render(
            TARGET_PROMPT,
            {
                "name": observation.get("name", agent_id),
                "coop": getattr(self.sim, "coop_name", "the cooperative"),
                "principal": getattr(self.sim, "principal_name", "the principal"),
                "visibility_line": observation.get("workspace_notice", ""),
            },
        )
        user = self.sim.rendered_for(agent_id) if self.sim is not None else json.dumps(observation)
        user = (
            user
            + "\n\nReply with one JSON object for an Action, or a JSON list of them. "
            "Fields follow the action schema. No other text."
        )
        try:
            text, tokens_in, tokens_out = self._complete(system, user)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            return [{"kind": "malformed", "raw_error": str(exc)}]
        if self.sim is not None:
            self.sim.record_llm_call(
                actor=agent_id,
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                usd_cost=estimate_usd(self.model, tokens_in, tokens_out),
            )
        try:
            return parse_actions(text)
        except (json.JSONDecodeError, ValueError):
            return [text]

    def _complete(self, system: str, user: str) -> tuple[str, int, int]:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
        parts = body.get("content") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        usage = body.get("usage") or {}
        return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


# Imported so a wrapper can build Action objects without a second import path.
__all__ = ["AnthropicDriver", "PRICE_PER_MILLION", "estimate_usd", "parse_actions", "Action"]
