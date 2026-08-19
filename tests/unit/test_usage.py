from src.agents import usage


def test_record_usage_computes_cost_from_pricing_table():
    usage.start_job_budget("job-1", budget_usd=10.0)

    cost = usage.record_usage("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)

    assert cost == 2.50 + 10.0
    budget = usage.get_job_budget()
    assert budget.total_cost_usd == 12.50
    assert budget.total_input_tokens == 1_000_000
    assert budget.total_output_tokens == 1_000_000


def test_record_usage_unknown_model_falls_back_to_default_pricing():
    usage.start_job_budget("job-2", budget_usd=10.0)

    cost = usage.record_usage("some-future-model", input_tokens=1_000_000, output_tokens=0)

    assert cost == usage._DEFAULT_PRICING[0]


def test_is_over_budget_false_below_threshold():
    usage.start_job_budget("job-3", budget_usd=1.0)
    usage.record_usage("gpt-4o-mini", input_tokens=1000, output_tokens=1000)

    assert usage.is_over_budget() is False


def test_is_over_budget_true_at_or_above_threshold():
    usage.start_job_budget("job-4", budget_usd=0.001)
    usage.record_usage("gpt-4o", input_tokens=100_000, output_tokens=100_000)

    assert usage.is_over_budget() is True


def test_is_over_budget_false_with_no_active_job():
    usage._current_budget.set(None)
    assert usage.is_over_budget() is False


def test_record_usage_is_noop_without_active_job():
    usage._current_budget.set(None)
    cost = usage.record_usage("gpt-4o", input_tokens=1000, output_tokens=1000)
    assert cost > 0  # still returns the computed cost
    assert usage.get_job_budget() is None  # but nothing is accumulated anywhere
