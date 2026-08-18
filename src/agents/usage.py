"""Per-job cumulative cost tracking -- the independent loop-safety guard
that catches what the iteration cap can't: a single call at high effort
against a huge context blowing past budget faster than the iteration
counter would notice. Uses a ContextVar so every agent call site
(call_structured, run_tool_loop) can record usage without threading a
tracker object through every function signature.

Pricing below is Anthropic's published per-million-token rate as of this
project's initial build -- verify against https://www.anthropic.com/pricing
before relying on this for real budget enforcement, since prices change.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field

# USD per million tokens: (input, output)
PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}
_DEFAULT_PRICING = (3.0, 15.0)  # fall back to Sonnet-tier pricing for unrecognized model strings


@dataclass
class JobBudget:
    job_id: str
    budget_usd: float
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    calls: list[dict] = field(default_factory=list)


_current_budget: ContextVar[JobBudget | None] = ContextVar("_current_budget", default=None)


def start_job_budget(job_id: str, budget_usd: float) -> None:
    _current_budget.set(JobBudget(job_id=job_id, budget_usd=budget_usd))


def get_job_budget() -> JobBudget | None:
    return _current_budget.get()


def record_usage(model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns the cost of this single call in USD. No-op (returns 0.0) if
    no job budget context is active -- e.g. ad-hoc scripts/tests that don't
    call start_job_budget first.
    """
    input_rate, output_rate = PRICING_PER_MILLION_TOKENS.get(model, _DEFAULT_PRICING)
    cost = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate

    budget = _current_budget.get()
    if budget is not None:
        budget.total_cost_usd += cost
        budget.total_input_tokens += input_tokens
        budget.total_output_tokens += output_tokens
        budget.calls.append(
            {"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}
        )
    return cost


def is_over_budget() -> bool:
    budget = _current_budget.get()
    if budget is None:
        return False
    return budget.total_cost_usd >= budget.budget_usd
