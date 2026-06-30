"""Tests for the spending plan module."""

from decimal import Decimal
import pytest
from brr.spending_plan import (
    SpendingPlan,
    calculate_spending_plan,
    format_spending_plan_message,
    RELAY_SERVICE_FEE_RATE,
)


def test_spending_plan_basic():
    """Test basic SpendingPlan creation."""
    plan = SpendingPlan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=1000,
        estimated_output_tokens=500,
        provider_cost_usd=Decimal("0.10"),
        relay_service_fee_usd=Decimal("0.012"),
        total_estimated_cost_usd=Decimal("0.112"),
        per_run_cap_usd=Decimal("1.00"),
        relay_balance_usd=Decimal("5.00"),
        consent_state="pending",
    )
    assert plan.reason == "local_quota_exhausted"
    assert plan.model == "gpt-5-codex-mini"
    assert plan.provider == "openai"
    assert plan.total_estimated_cost_usd == Decimal("0.112")
    assert plan.consent_state == "pending"


def test_calculate_spending_plan():
    """Test spending plan calculation with token rates."""
    plan = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=10000,
        estimated_output_tokens=5000,
        provider_cost_per_input_mtok=Decimal("1.00"),  # $1 per 1M tokens
        provider_cost_per_output_mtok=Decimal("4.00"),  # $4 per 1M tokens
        per_run_cap_usd=Decimal("1.00"),
        relay_balance_usd=Decimal("10.00"),
    )
    # Input: 10k tokens * ($1 / 1M) = $0.01
    # Output: 5k tokens * ($4 / 1M) = $0.02
    # Provider total: $0.03
    # Relay fee: $0.03 * 12% = $0.0036 ≈ $0.00 (rounds to $0.00)
    # Total: $0.03 + $0.00 = $0.03
    assert plan.provider_cost_usd == Decimal("0.03")
    assert plan.relay_service_fee_usd == Decimal("0.00")
    assert plan.total_estimated_cost_usd == Decimal("0.03")
    assert plan.consent_state == "pending"


def test_relay_fee_calculation():
    """Test relay service fee is calculated correctly (12%)."""
    plan = calculate_spending_plan(
        reason="no_local_runner",
        model="gpt-5-codex",
        provider="openai",
        estimated_input_tokens=100000,
        estimated_output_tokens=50000,
        provider_cost_per_input_mtok=Decimal("0.50"),
        provider_cost_per_output_mtok=Decimal("1.50"),
        per_run_cap_usd=Decimal("5.00"),
    )
    # Input: 100k tokens * ($0.50 / 1M) = $0.05
    # Output: 50k tokens * ($1.50 / 1M) = $0.075
    # Provider total (before rounding): $0.125
    # Relay fee (before rounding): $0.125 * 12% = $0.015
    # After rounding: $0.12 (provider), $0.02 (fee), $0.14 (total)
    assert plan.provider_cost_usd == Decimal("0.12")
    assert plan.relay_service_fee_usd == Decimal("0.02")
    assert plan.total_estimated_cost_usd == Decimal("0.14")


def test_spending_plan_within_cap():
    """Test is_within_cap check."""
    plan = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=5000,
        estimated_output_tokens=2000,
        provider_cost_per_input_mtok=Decimal("1.00"),
        provider_cost_per_output_mtok=Decimal("4.00"),
        per_run_cap_usd=Decimal("1.00"),
    )
    # Cost: $0.005 + $0.008 = $0.013 (well under cap)
    assert plan.is_within_cap() is True

    # Test over cap
    plan_over = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=1000000,
        estimated_output_tokens=1000000,
        provider_cost_per_input_mtok=Decimal("1.00"),
        provider_cost_per_output_mtok=Decimal("4.00"),
        per_run_cap_usd=Decimal("1.00"),
    )
    # Cost: $1.00 + $4.00 = $5.00 (way over $1.00 cap)
    assert plan_over.is_within_cap() is False


def test_spending_plan_sufficient_balance():
    """Test has_sufficient_balance check."""
    plan = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=10000,
        estimated_output_tokens=5000,
        provider_cost_per_input_mtok=Decimal("1.00"),
        provider_cost_per_output_mtok=Decimal("4.00"),
        per_run_cap_usd=Decimal("1.00"),
        relay_balance_usd=Decimal("10.00"),
    )
    assert plan.has_sufficient_balance() is True

    # Test insufficient balance
    plan_low = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=10000,
        estimated_output_tokens=5000,
        provider_cost_per_input_mtok=Decimal("1.00"),
        provider_cost_per_output_mtok=Decimal("4.00"),
        per_run_cap_usd=Decimal("1.00"),
        relay_balance_usd=Decimal("0.01"),
    )
    assert plan_low.has_sufficient_balance() is False


def test_spending_plan_unknown_costs():
    """Test handling of unknown token rates."""
    plan = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=10000,
        estimated_output_tokens=5000,
        provider_cost_per_input_mtok=None,  # Unknown
        provider_cost_per_output_mtok=None,  # Unknown
        per_run_cap_usd=Decimal("1.00"),
    )
    assert plan.provider_cost_usd is None
    assert plan.relay_service_fee_usd is None
    assert plan.total_estimated_cost_usd is None
    assert plan.is_within_cap() is None
    assert plan.has_sufficient_balance() is None


def test_spending_plan_to_dict():
    """Test serialization to dict."""
    plan = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=10000,
        estimated_output_tokens=5000,
        provider_cost_per_input_mtok=Decimal("1.00"),
        provider_cost_per_output_mtok=Decimal("4.00"),
        per_run_cap_usd=Decimal("1.00"),
        relay_balance_usd=Decimal("10.00"),
    )
    plan_dict = plan.to_dict()
    assert plan_dict["reason"] == "local_quota_exhausted"
    assert plan_dict["model"] == "gpt-5-codex-mini"
    assert plan_dict["provider"] == "openai"
    assert isinstance(plan_dict["provider_cost_usd"], str)
    assert isinstance(plan_dict["relay_service_fee_usd"], str)
    assert isinstance(plan_dict["total_estimated_cost_usd"], str)


def test_format_spending_plan_message():
    """Test spending plan message formatting."""
    plan = calculate_spending_plan(
        reason="local_quota_exhausted",
        model="gpt-5-codex-mini",
        provider="openai",
        estimated_input_tokens=10000,
        estimated_output_tokens=5000,
        provider_cost_per_input_mtok=Decimal("1.00"),
        provider_cost_per_output_mtok=Decimal("4.00"),
        per_run_cap_usd=Decimal("1.00"),
        relay_balance_usd=Decimal("10.00"),
    )
    message = format_spending_plan_message(plan)
    assert "quota exhausted" in message.lower()
    assert "gpt-5-codex-mini" in message
    assert "openai" in message
    assert "$" in message
    assert "Approve" in message
