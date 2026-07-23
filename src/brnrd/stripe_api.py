"""Thin Stripe REST client (#53, kb design-billing.md §"Stripe integration
shape").

Deliberately not the ``stripe`` SDK: the four calls brnrd makes are plain
form-encoded POSTs, and the webhook side needs only ``hmac`` — same
lean-dependency posture as the GitHub adapters (httpx, module functions,
monkeypatched in tests). Checkout owns all PCI/SCA surface; brnrd never
touches card data.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0
# Stripe's recommended default tolerance for webhook timestamp skew.
SIGNATURE_TOLERANCE_S = 300


class StripeError(RuntimeError):
    """A Stripe API call failed; ``detail`` carries Stripe's error message."""

    def __init__(self, detail: str, status_code: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _post(settings: Settings, path: str, data: dict) -> dict:
    if not settings.stripe_api_key:
        raise StripeError("stripe is not configured (BRNRD_STRIPE_API_KEY unset)", status_code=503)
    try:
        response = httpx.post(
            f"{settings.stripe_api_base_url}/v1{path}",
            data=data,
            auth=(settings.stripe_api_key, ""),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        # Genuine gateway failure — we never reached Stripe. 502 is honest here.
        logger.warning("stripe %s transport error: %s", path, exc)
        raise StripeError(f"stripe request failed: {exc}", status_code=502) from exc
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message") or f"stripe error {response.status_code}"
        # Always log — Stripe's message is the whole diagnosis (bad price,
        # missing tax code, unsupported param) and uvicorn's access line alone
        # never carries it. A 4xx from Stripe is an actionable request/config
        # error, so surface it with a client-visible status: a 502 body gets
        # replaced by proxies/CDNs (Cloudflare's "error code: 502" page) and the
        # message never reaches the browser or the operator. Reserve 502 for
        # Stripe-side 5xx, which are true upstream faults.
        logger.warning("stripe %s -> %s: %s", path, response.status_code, message)
        status_code = 400 if 400 <= response.status_code < 500 else 502
        raise StripeError(message, status_code=status_code)
    return body


def create_customer(settings: Settings, *, account_id: str, email: str | None) -> dict:
    data: dict = {"metadata[brnrd_account_id]": account_id}
    if email:
        data["email"] = email
    return _post(settings, "/customers", data)


def create_subscription_checkout(
    settings: Settings,
    *,
    customer_id: str,
    price_id: str,
    account_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    return _post(
        settings,
        "/checkout/sessions",
        {
            "mode": "subscription",
            "customer": customer_id,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            # Promotion codes typed by the customer at checkout (2026-07-22
            # ask: near-free self-test subscriptions without a parallel
            # price). Codes are minted in the Stripe dashboard; nothing
            # brnrd-side needs to know they exist.
            "allow_promotion_codes": "true",
            "success_url": success_url,
            # Tax is handled by the account's Managed Payments (Stripe as
            # merchant of record), which rejects an explicit automatic_tax
            # parameter ("Unsupported parameter: automatic_tax"). Tax codes
            # live on the Stripe Products. To instead run classic Stripe Tax,
            # pass managed_payments[enabled]=false *and* automatic_tax[enabled]
            # =true here and set a default tax code in Tax settings.
            #
            # tax_id_collection is rejected the same way (probed live
            # 2026-07-23): under merchant-of-record, Stripe owns VAT
            # calculation and remittance outright, collects no buyer tax IDs,
            # and offers no B2B reverse charge. Do not re-add the parameter.
            # A future business tier that needs VAT-ID invoices means either
            # separate business-use products or leaving Managed Payments —
            # a pricing/liability decision, not a checkout flag.
            # Products carry txcd_10105003 (AIaaS - personal use) as of
            # 2026-07-23; the business-use sibling is txcd_10105004.
            "cancel_url": cancel_url,
            "metadata[brnrd_account_id]": account_id,
            "metadata[brnrd_purpose]": "subscription",
            "subscription_data[metadata][brnrd_account_id]": account_id,
        },
    )


def create_topup_checkout(
    settings: Settings,
    *,
    customer_id: str | None,
    credits: int,
    account_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """One-shot wallet top-up: inline price_data at $0.01/credit, no
    card-on-file (``setup_future_usage`` deliberately absent)."""
    data = {
        "mode": "payment",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": "1",
        "line_items[0][price_data][product_data][name]": "Brnrd Wallet Top-up",
        "line_items[0][quantity]": str(credits),
        "success_url": success_url,
        # See create_subscription_checkout: Managed Payments owns tax and
        # rejects an explicit automatic_tax parameter.
        "cancel_url": cancel_url,
        "metadata[brnrd_account_id]": account_id,
        "metadata[brnrd_purpose]": "wallet_topup",
        "metadata[brnrd_credits]": str(credits),
        "payment_intent_data[metadata][brnrd_account_id]": account_id,
        "payment_intent_data[metadata][brnrd_credits]": str(credits),
    }
    if customer_id:
        data["customer"] = customer_id
    return _post(settings, "/checkout/sessions", data)


def create_portal_session(settings: Settings, *, customer_id: str, return_url: str) -> dict:
    return _post(
        settings,
        "/billing_portal/sessions",
        {"customer": customer_id, "return_url": return_url},
    )


def set_subscription_cancel_at_period_end(
    settings: Settings, *, subscription_id: str, cancel: bool
) -> dict:
    return _post(
        settings,
        f"/subscriptions/{subscription_id}",
        {"cancel_at_period_end": "true" if cancel else "false"},
    )


def verify_webhook_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    *,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
    now: float | None = None,
) -> bool:
    """Verify Stripe's ``Stripe-Signature: t=...,v1=...`` scheme."""
    if not secret or not signature_header:
        return False
    timestamp = ""
    candidates: list[str] = []
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp or not candidates:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance_s:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
