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
import time
import urllib.error
import urllib.request
from typing import Any, Callable

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


# Models that reject temperature, top_p, and top_k. A 400 whose message names
# one of those parameters is added here for the rest of the process.
OMIT_SAMPLING_MODELS = {"claude-sonnet-5"}
MAX_DRIVER_ATTEMPTS = 3
SleepFn = Callable[[float], None]


class DriverError(RuntimeError):
    """An API or driver failure. This is not an invalid action."""

    def __init__(
        self,
        message: str,
        *,
        error_class: str,
        status_code: int | None = None,
        attempt: int = 1,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.status_code = status_code
        self.attempt = attempt
        self.retryable = retryable


def omits_sampling(model: str) -> bool:
    name = (model or "").strip().lower()
    if name in OMIT_SAMPLING_MODELS or name.startswith("claude-sonnet-5"):
        return True
    return False


def note_sampling_rejected(model: str) -> None:
    """Remember a model that rejected sampling parameters."""
    if model:
        OMIT_SAMPLING_MODELS.add(model.strip().lower())


def sampling_rejected_message(message: str) -> bool:
    low = (message or "").lower()
    if not any(token in low for token in ("temperature", "top_p", "top_k")):
        return False
    return any(token in low for token in ("deprecated", "not supported", "does not support", "unsupported"))


def sent_temperature(model: str, temperature: float | None) -> float | None:
    """Temperature actually placed on the request. Null when the model rejects it."""
    if omits_sampling(model):
        return None
    return temperature


def sampling_label(model: str) -> str | None:
    """``model_default`` when sampling params are omitted. Mock and scripted stay unset."""
    if omits_sampling(model):
        return "model_default"
    return None


def sampling_fields(
    model: str,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Request fields for sampling. Empty when the model rejects them."""
    if omits_sampling(model):
        return {}
    fields: dict[str, Any] = {}
    if temperature is not None:
        fields["temperature"] = temperature
    if top_p is not None:
        fields["top_p"] = top_p
    if top_k is not None:
        fields["top_k"] = top_k
    return fields


def is_retryable_status(status_code: int | None, error_class: str = "") -> bool:
    """429, 5xx, and timeouts. Other 4xx, including 400 and 401, are not."""
    if error_class in {"TimeoutError", "URLError"}:
        return True
    if status_code == 429 or status_code == 408:
        return True
    if status_code is not None and 500 <= int(status_code) <= 599:
        return True
    return False


def is_fatal_driver_error(exc: DriverError) -> bool:
    """Config and auth errors stop the run. Exhausted transient errors use the rate gate."""
    if exc.status_code == 429 or exc.status_code == 408:
        return False
    if exc.status_code is not None and 500 <= int(exc.status_code) <= 599:
        return False
    if exc.error_class in {"TimeoutError", "URLError"}:
        return False
    if exc.status_code is not None and 400 <= int(exc.status_code) < 500:
        return True
    return not exc.retryable


def backoff_seconds(attempt: int) -> float:
    """Sleep after a failed attempt, before the next one. Attempt 1 waits 0.5s."""
    return 0.5 * (2 ** (max(attempt, 1) - 1))


def _http_message(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = str(exc)
    raw = raw.strip()
    if not raw:
        return str(exc)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:500]
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:500]
    return raw[:500]


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
        self.sleep: SleepFn = time.sleep
        self.urlopen = urllib.request.urlopen

    def bind(self, sim: Any) -> None:
        self.sim = sim

    def preflight(self) -> None:
        """One tiny call. A failure here must abort before any episode events."""
        self._complete("Reply with ok.", "ok", max_tokens=1)

    def act(self, agent_id: str, observation: dict[str, Any]) -> list[Any]:
        if not self.api_key:
            raise DriverError(
                "ANTHROPIC_API_KEY is not set",
                error_class="AuthError",
                status_code=401,
                attempt=1,
                retryable=False,
            )
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
        text, tokens_in, tokens_out, retries = self._complete(system, user)
        if self.sim is not None:
            self.sim.record_llm_call(
                actor=agent_id,
                model=self.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                usd_cost=estimate_usd(self.model, tokens_in, tokens_out),
                attempts=1 + len(retries),
                retries=retries,
            )
        try:
            return parse_actions(text)
        except (json.JSONDecodeError, ValueError):
            return [text]

    def request_body(self, system: str, user: str, max_tokens: int | None = None) -> dict[str, Any]:
        temperature = None
        if self.sim is not None and getattr(self.sim, "config", None) is not None:
            temperature = self.sim.config.temperature
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        body.update(sampling_fields(self.model, temperature))
        return body

    def _complete(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> tuple[str, int, int, list[dict[str, Any]]]:
        retries: list[dict[str, Any]] = []
        adapted = False
        attempt = 1
        while attempt <= MAX_DRIVER_ATTEMPTS:
            body = self.request_body(system, user, max_tokens=max_tokens)
            sent_sampling = any(key in body for key in ("temperature", "top_p", "top_k"))
            try:
                text, tokens_in, tokens_out = self._exchange(body)
                return text, tokens_in, tokens_out, retries
            except DriverError as exc:
                exc.attempt = attempt
                if (
                    not adapted
                    and sent_sampling
                    and exc.status_code == 400
                    and sampling_rejected_message(str(exc))
                ):
                    note_sampling_rejected(self.model)
                    adapted = True
                    retries.append(_retry_row(exc))
                    continue
                if exc.retryable and attempt < MAX_DRIVER_ATTEMPTS:
                    retries.append(_retry_row(exc))
                    self.sleep(backoff_seconds(attempt))
                    attempt += 1
                    continue
                exc.retryable = False
                raise
        raise DriverError("driver retries exhausted", error_class="DriverError", attempt=attempt, retryable=False)

    def _exchange(self, body: dict[str, Any]) -> tuple[str, int, int]:
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            message = _http_message(exc)
            raise DriverError(
                message,
                error_class="HTTPError",
                status_code=int(exc.code),
                retryable=is_retryable_status(int(exc.code), "HTTPError"),
            ) from exc
        except TimeoutError as exc:
            raise DriverError(
                str(exc) or "timeout",
                error_class="TimeoutError",
                retryable=True,
            ) from exc
        except urllib.error.URLError as exc:
            raise DriverError(
                str(exc.reason if getattr(exc, "reason", None) else exc),
                error_class="URLError",
                retryable=True,
            ) from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DriverError(
                "API response was not JSON",
                error_class="JSONDecodeError",
                retryable=False,
            ) from exc
        if not isinstance(parsed, dict):
            raise DriverError(
                "API response was not an object",
                error_class="ValueError",
                retryable=False,
            )
        parts = parsed.get("content") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        usage = parsed.get("usage") or {}
        return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _retry_row(exc: DriverError) -> dict[str, Any]:
    return {
        "attempt": exc.attempt,
        "error_class": exc.error_class,
        "status_code": exc.status_code,
        "message": str(exc),
    }


# Imported so a wrapper can build Action objects without a second import path.
__all__ = [
    "AnthropicDriver",
    "DriverError",
    "PRICE_PER_MILLION",
    "estimate_usd",
    "parse_actions",
    "Action",
    "omits_sampling",
    "sent_temperature",
    "sampling_fields",
    "sampling_label",
]
