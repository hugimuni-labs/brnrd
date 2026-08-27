// Dashboard billing surface: the SPA leg of the #53 billing API
// (`src/brnrd/routers/billing.py`). Same seam as every other authenticated
// dashboard fetch — the brnrd_session cookie, `credentials: 'include'` —
// which the billing router accepts since `require_account_or_session`
// (auth.py) extended the bearer dependency to the session cookie.
//
// Scope (maintainer steer, 2026-07-21): subscription only — state, checkout,
// cancel/resume, Customer Portal. No wallet/top-up/ledger UI: the
// subscription removes the free tier's headroom limits and keeps the lights
// on, and the panel says exactly that — no credits framing for a product that
// doesn't exist yet.
//
// All money *state* lives server-side (Stripe webhook → db); this module
// only reads it and mints Checkout/Portal redirect URLs. The current pricing
// decision (2026-08-27) is one subscriber offer: $7/mo · $70/yr. Historical
// supporter cohorts remain server-side only so existing subscriptions can be
// classified without changing their Stripe Price.

export interface SubscriptionState {
	tier: string;
	status: string | null;
	cohort: string | null;
	cadence: string | null;
	cancel_at_period_end: boolean;
	current_period_end: string | null;
}

export type Cadence = 'monthly' | 'annual';

export class BillingAuthError extends Error {}

async function getJson<T>(url: string, fetchImpl: typeof fetch): Promise<T> {
	const res = await fetchImpl(url, { credentials: 'include' });
	if (res.status === 401) throw new BillingAuthError('not signed in');
	if (!res.ok) throw new Error(`billing fetch failed: ${res.status}`);
	return (await res.json()) as T;
}

async function postJson<T>(
	url: string,
	body: Record<string, unknown>,
	fetchImpl: typeof fetch
): Promise<T> {
	const res = await fetchImpl(url, {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (res.status === 401) throw new BillingAuthError('not signed in');
	if (!res.ok) {
		let detail = '';
		try {
			const payload = (await res.json()) as { detail?: unknown };
			if (typeof payload.detail === 'string') detail = payload.detail;
		} catch {
			// non-JSON error body — fall through to the status line
		}
		throw new Error(detail || `billing request failed: ${res.status}`);
	}
	return (await res.json()) as T;
}

export function fetchSubscription(fetchImpl: typeof fetch = fetch): Promise<SubscriptionState> {
	return getJson('/v1/accounts/subscription', fetchImpl);
}

/** Mints a subscription Checkout session; resolves to the Stripe-hosted URL
 * the caller must redirect to. The server selects the public Price. */
export async function startSubscriptionCheckout(
	cadence: Cadence,
	fetchImpl: typeof fetch = fetch
): Promise<string> {
	const out = await postJson<{ checkout_url: string }>(
		'/v1/accounts/subscription/checkout',
		{ cadence },
		fetchImpl
	);
	if (!out.checkout_url) throw new Error('checkout session came back without a URL');
	return out.checkout_url;
}

export function cancelSubscription(fetchImpl: typeof fetch = fetch): Promise<SubscriptionState> {
	return postJson('/v1/accounts/subscription/cancel', {}, fetchImpl);
}

export function resumeSubscription(fetchImpl: typeof fetch = fetch): Promise<SubscriptionState> {
	return postJson('/v1/accounts/subscription/resume', {}, fetchImpl);
}

/** Mints a Stripe Customer Portal session (card, invoices, cancel). */
export async function startBillingPortal(fetchImpl: typeof fetch = fetch): Promise<string> {
	const out = await postJson<{ portal_url: string }>(
		'/v1/accounts/subscription/portal',
		{},
		fetchImpl
	);
	if (!out.portal_url) throw new Error('portal session came back without a URL');
	return out.portal_url;
}

// --- pricing (display only — Stripe is authoritative at checkout) ----------

export const PRICING = {
	monthly: 7,
	annual: 70
} as const;

/** The single price shown next to the subscribe CTA. */
export function subscribeOffer(cadence: Cadence): { usd: number; label: string } {
	const usd = PRICING[cadence];
	return { usd, label: cadence === 'monthly' ? `$${usd}/mo` : `$${usd}/yr` };
}

// --- return-param notices ---------------------------------------------------

export interface BillingNotice {
	kind: 'success' | 'quiet';
	text: string;
}

/** Maps the Checkout return params (`?billing=…`, set by the server's
 * success/cancel URLs) to a one-line banner. Success copy stays honest:
 * entitlements ride the webhook and land incrementally, so "confirming",
 * never "done". The topup-* params have no UI leg anymore but the server
 * still mints those return URLs for API-driven top-ups, so they render
 * rather than vanishing. Unknown values render nothing. */
export function billingReturnNotice(search: string): BillingNotice | null {
	const value = new URLSearchParams(search).get('billing');
	switch (value) {
		case 'subscribed':
			return {
				kind: 'success',
				text: 'checkout complete — thank you. Stripe is confirming; the subscription lands here incrementally.'
			};
		case 'topup-complete':
			return {
				kind: 'success',
				text: 'top-up checkout complete — Stripe is confirming the payment.'
			};
		case 'canceled':
			return { kind: 'quiet', text: 'checkout canceled — nothing was charged.' };
		case 'topup-canceled':
			return { kind: 'quiet', text: 'top-up canceled — nothing was charged.' };
		default:
			return null;
	}
}

/** Strips the `billing` return param so a reload doesn't re-announce. */
export function withoutBillingParam(href: string): string {
	const url = new URL(href);
	url.searchParams.delete('billing');
	return url.pathname + (url.search ? url.search : '') + url.hash;
}

/** Compact `YYYY-MM-DD` for renew/end dates, UTC — empty on unparseable. */
export function dateLabel(iso: string): string {
	const t = Date.parse(iso);
	if (!Number.isFinite(t)) return '';
	const d = new Date(t);
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}
