"""One live seat: reserve a price, call the model, settle, parse an action."""

from __future__ import annotations

from typing import Protocol

from coop.agents.budget import Budget, BudgetExceeded
from coop.agents.llm import LLMResult, parse_action
from coop.agents.pricing import approx_tokens, price_tokens
from coop.agents.prompts import member_system
from coop.schema import Action, RunConfig


class Backend(Protocol):
    model: str

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> LLMResult: ...


class MemberAgent:
    """Budget wrapper around one completion. Used by the live policy and tests."""

    def __init__(self, config: RunConfig, actor: str, backend: Backend, budget: Budget):
        self.config = config
        self.actor = actor
        self.backend = backend
        self.budget = budget
        self.system = member_system()
        self.seat_model = config.model

    def act(self, user: str) -> tuple[Action, LLMResult]:
        text = user if isinstance(user, str) else ""
        estimate = 0.0
        if getattr(self.backend, "model", "mock") != "mock":
            tokens_in = approx_tokens(self.system) + approx_tokens(text)
            max_tokens = getattr(self.backend, "max_tokens", 512)
            estimate = price_tokens(self.seat_model, tokens_in, max_tokens)
            self.budget.reserve(estimate)
        try:
            result = self.backend.complete(
                system=self.system,
                user=text,
                temperature=self.config.temperature,
            )
        except Exception:
            if estimate:
                self.budget.settle(estimate, 0.0)
            raise
        try:
            if estimate:
                self.budget.settle(estimate, result.usd_cost)
            elif result.usd_cost:
                self.budget.reserve(result.usd_cost)
        except BudgetExceeded as exc:
            exc.result = result  # type: ignore[attr-defined]
            raise
        return parse_action(result.text), result
