"""Spending plan — estimation and consent for relay billing.

When a run would rely on paid brnrd LLM relay (because local quota is
exhausted or unavailable), a spending plan is emitted for user approval.
The plan projects the expected cost envelope and gates the run's continuation
on explicit consent.

Design: decision-llm-relay.md (accepted 2026-06-15) and design-runner-cores.md
step 9 (spending-plan consent). A spending plan is trustworthy only when the
user sees the projected cost before spend, not after.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


# Relay service fee rate: 10–15% of provider cost (exact rate locked in design-billing.md).
# Using 12% as the middle ground for now — will be reconciled when design-billing is updated.
RELAY_SERVICE_FEE_RATE = Decimal("0.12")  # 12% of provider cost


@dataclass(frozen=True)
class SpendingPlan:
    """One spending plan: a projection and cap envelope for relay use.

    The spending plan projects the expected cost of a run that needs to fall back
    to brnrd LLM relay (because local quota is absent/exhausted). It includes:
    - the model/runner being used
    - estimated provider cost (input tokens + output tokens)
    - relay service fee (percentage of provider cost)
    - per-run cap (wallet-enforced hard stop)
    - current relay balance
    - reason for relay fallback (quota exhausted, auth error, etc.)

    The plan is emitted to the user for approval before the run commits to relay spend.
    """

    reason: str
    """Why relay is needed: 'local_quota_exhausted', 'no_local_runner', 'auth_error', etc."""

    model: str
    """The model name being used (e.g., 'gpt-5-codex-mini')."""

    provider: str
    """The provider ('openai' for Codex relay, 'anthropic' for Claude relay, etc.)."""

    estimated_input_tokens: int = 0
    """Estimated input tokens for this run (context + prompt)."""

    estimated_output_tokens: int = 0
    """Estimated output tokens for this run (response budget)."""

    provider_cost_per_input_mtok: Decimal | None = None
    """Provider cost per million input tokens."""

    provider_cost_per_output_mtok: Decimal | None = None
    """Provider cost per million output tokens."""

    provider_cost_usd: Decimal | None = None
    """Calculated provider cost (input + output at current rates), or None if unknown."""

    relay_service_fee_usd: Decimal | None = None
    """Calculated relay service fee (RELAY_SERVICE_FEE_RATE * provider_cost), or None if unknown."""

    total_estimated_cost_usd: Decimal | None = None
    """Total estimated cost (provider_cost + relay_service_fee), or None if unknown."""

    per_run_cap_usd: Decimal = Decimal("1.00")
    """Hard cap for this run (wallet enforces this server-side)."""

    relay_balance_usd: Decimal | None = None
    """Current relay balance, or None if unknown."""

    per_day_cap_usd: Decimal = Decimal("5.00")
    """Daily cap for relay usage, or None if not configured."""

    wallet_balance_usd: Decimal | None = None
    """Current wallet balance (if available from brnrd)."""

    consent_state: str = "pending"
    """'pending' (awaiting approval), 'approved', 'denied', or 'capped' (hit cap)."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON emission and portal exposure."""
        return {
            "reason": self.reason,
            "model": self.model,
            "provider": self.provider,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "provider_cost_per_input_mtok": (
                str(self.provider_cost_per_input_mtok)
                if self.provider_cost_per_input_mtok is not None
                else None
            ),
            "provider_cost_per_output_mtok": (
                str(self.provider_cost_per_output_mtok)
                if self.provider_cost_per_output_mtok is not None
                else None
            ),
            "provider_cost_usd": str(self.provider_cost_usd) if self.provider_cost_usd is not None else None,
            "relay_service_fee_usd": str(self.relay_service_fee_usd) if self.relay_service_fee_usd is not None else None,
            "total_estimated_cost_usd": str(self.total_estimated_cost_usd) if self.total_estimated_cost_usd is not None else None,
            "per_run_cap_usd": str(self.per_run_cap_usd),
            "relay_balance_usd": str(self.relay_balance_usd) if self.relay_balance_usd is not None else None,
            "per_day_cap_usd": str(self.per_day_cap_usd),
            "wallet_balance_usd": str(self.wallet_balance_usd) if self.wallet_balance_usd is not None else None,
            "consent_state": self.consent_state,
        }

    def is_within_cap(self) -> bool | None:
        """True if total_estimated_cost <= per_run_cap; None if costs unknown."""
        if self.total_estimated_cost_usd is None:
            return None
        return self.total_estimated_cost_usd <= self.per_run_cap_usd

    def has_sufficient_balance(self) -> bool | None:
        """True if relay_balance >= total_estimated_cost; None if balance unknown."""
        if self.relay_balance_usd is None or self.total_estimated_cost_usd is None:
            return None
        return self.relay_balance_usd >= self.total_estimated_cost_usd


def calculate_spending_plan(
    reason: str,
    model: str,
    provider: str,
    *,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    provider_cost_per_input_mtok: Decimal | None = None,
    provider_cost_per_output_mtok: Decimal | None = None,
    per_run_cap_usd: Decimal = Decimal("1.00"),
    relay_balance_usd: Decimal | None = None,
    wallet_balance_usd: Decimal | None = None,
    per_day_cap_usd: Decimal = Decimal("5.00"),
) -> SpendingPlan:
    """Create a spending plan with calculated costs.

    Calculates provider cost and relay service fee from token estimates and
    per-token rates. Both inputs are optional; if either is missing, the totals
    are None (unknown).

    Args:
        reason: Why relay is needed (e.g., 'local_quota_exhausted').
        model: The model name.
        provider: The provider name.
        estimated_input_tokens: Estimated input token count.
        estimated_output_tokens: Estimated output token count.
        provider_cost_per_input_mtok: Cost per million input tokens.
        provider_cost_per_output_mtok: Cost per million output tokens.
        per_run_cap_usd: Hard cap for this run.
        relay_balance_usd: Current relay balance (if known).
        wallet_balance_usd: Current wallet balance (if known).
        per_day_cap_usd: Daily relay cap.

    Returns:
        A SpendingPlan with calculated costs and metadata.
    """
    provider_cost_usd = None
    relay_service_fee_usd = None
    total_estimated_cost_usd = None

    if provider_cost_per_input_mtok is not None and provider_cost_per_output_mtok is not None:
        input_cost = (Decimal(estimated_input_tokens) / Decimal("1_000_000")) * provider_cost_per_input_mtok
        output_cost = (Decimal(estimated_output_tokens) / Decimal("1_000_000")) * provider_cost_per_output_mtok
        provider_cost_usd = input_cost + output_cost

        # Calculate relay service fee
        relay_service_fee_usd = provider_cost_usd * RELAY_SERVICE_FEE_RATE
        total_estimated_cost_usd = provider_cost_usd + relay_service_fee_usd

        # Round to 2 decimal places for USD
        provider_cost_usd = provider_cost_usd.quantize(Decimal("0.01"))
        relay_service_fee_usd = relay_service_fee_usd.quantize(Decimal("0.01"))
        total_estimated_cost_usd = total_estimated_cost_usd.quantize(Decimal("0.01"))

    return SpendingPlan(
        reason=reason,
        model=model,
        provider=provider,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        provider_cost_per_input_mtok=provider_cost_per_input_mtok,
        provider_cost_per_output_mtok=provider_cost_per_output_mtok,
        provider_cost_usd=provider_cost_usd,
        relay_service_fee_usd=relay_service_fee_usd,
        total_estimated_cost_usd=total_estimated_cost_usd,
        per_run_cap_usd=per_run_cap_usd,
        relay_balance_usd=relay_balance_usd,
        wallet_balance_usd=wallet_balance_usd,
        per_day_cap_usd=per_day_cap_usd,
        consent_state="pending",
    )


def format_spending_plan_message(plan: SpendingPlan) -> str:
    """Format a spending plan as a user-friendly message for approval.

    Example output:
        Local Codex is out of weekly quota. I can continue with brnrd Codex relay.

        Runner: gpt-5-codex-mini (Codex Shell) via brnrd
        Model: gpt-5-codex-mini
        Cap: $0.75 for this run
        Billing: provider cost + 12% relay service fee, shown separately
        Balance: $4.20 relay balance

        Approve / Queue until local reset / Configure own runner
    """
    lines = [
        f"Local runner quota exhausted. Continuing with brnrd {plan.provider} relay.",
        "",
        f"Model: {plan.model} ({plan.provider})",
    ]

    if plan.total_estimated_cost_usd is not None:
        lines.append(f"Estimated cost: ${plan.provider_cost_usd} provider + ${plan.relay_service_fee_usd} relay fee = ${plan.total_estimated_cost_usd}")
    else:
        lines.append("Estimated cost: unknown (token estimate needed)")

    lines.append(f"Cap: ${plan.per_run_cap_usd} for this run")

    if plan.relay_balance_usd is not None:
        lines.append(f"Balance: ${plan.relay_balance_usd} relay balance")
    else:
        lines.append("Balance: unknown (wallet check needed)")

    lines.extend(
        [
            "",
            "Billing: provider cost + 12% relay service fee, shown separately",
            "",
            "Options: Approve / Queue until local reset / Configure own runner",
        ]
    )

    return "\n".join(lines)
