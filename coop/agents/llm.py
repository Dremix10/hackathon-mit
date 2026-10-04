"""LLM backends. ``MockLLM`` is deterministic. ``AnthropicLLM`` calls the Messages API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from coop.agents.pricing import approx_tokens, price_tokens
from coop.schema import Action

PostFn = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

KINDS = (
    "message",
    "submit_task",
    "transfer_credits",
    "invite",
    "accept_invite",
    "vote",
    "edit_doc",
    "submit_report",
    "reassign_task",
    "claim_task",
    "edit_board",
    "noop",
)

# Names from the earlier action list, mapped onto the simulator kinds.
_KIND_ALIASES = {
    "pass": "noop",
    "task_result": "submit_task",
    "credit_transfer": "transfer_credits",
    "report_submitted": "submit_report",
    "doc_edit": "edit_doc",
    "membership_change": "noop",
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    usd_cost: float


def _http_post(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"Anthropic HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Anthropic request failed: {exc.reason}") from exc


class AnthropicLLM:
    """Messages API. The key is read from ``ANTHROPIC_API_KEY`` and never logged."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 512,
        timeout: float = 60.0,
        post: PostFn | None = None,
        url: str = "https://api.anthropic.com/v1/messages",
    ):
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._post = post or _http_post
        self.url = url

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> LLMResult:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = self._post(self.url, headers, body, self.timeout)
        content = payload.get("content") or []
        texts = [block.get("text", "") for block in content if isinstance(block, dict)]
        text = "\n".join(part for part in texts if part).strip()
        usage = payload.get("usage") or {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        if tokens_in == 0 and tokens_out == 0:
            tokens_in = approx_tokens(system) + approx_tokens(user)
            tokens_out = approx_tokens(text)
        return LLMResult(
            text=text,
            model=str(payload.get("model") or self.model),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd_cost=price_tokens(self.model, tokens_in, tokens_out),
        )


class MockLLM:
    """Deterministic noop. Cost is zero. Token counts still reflect the prompt."""

    def __init__(self, model: str = "mock"):
        self.model = model

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> LLMResult:
        del temperature
        action = scripted_action(system, user)
        text = json.dumps(action.as_dict(), separators=(",", ":"))
        return LLMResult(
            text=text,
            model=self.model,
            tokens_in=approx_tokens(system) + approx_tokens(user),
            tokens_out=approx_tokens(text),
            usd_cost=0.0,
        )


def scripted_action(system: str, user: str) -> Action:
    """Ordinary mock reply. The recruiter ladder is ``coop.sim.insider``."""
    del system, user
    return Action(kind="noop")


def parse_action(text: str) -> Action:
    """Pull the first JSON object out of a model reply and map it to ``Action``."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return Action(kind="noop")
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return Action(kind="noop")
    if not isinstance(raw, dict):
        return Action(kind="noop")
    kind = raw.get("kind") or raw.get("type") or "noop"
    kind = _KIND_ALIASES.get(str(kind), str(kind))
    if kind not in KINDS:
        return Action(kind="noop")
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    data: dict[str, Any] = {"kind": kind}
    fields = set(Action.__dataclass_fields__)  # type: ignore[attr-defined]
    for key, value in {**payload, **raw}.items():
        if key in fields and key not in {"kind"} and value is not None:
            data[key] = value
    if "answer" not in data and "tests_passed" in payload:
        data["answer"] = "pass" if payload["tests_passed"] else "fail"
    channel = data.get("channel")
    if channel not in (None, "public", "private", "principal", "system"):
        data["channel"] = "public"
    return Action.from_obj(data)
