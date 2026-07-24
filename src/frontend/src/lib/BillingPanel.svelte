<script lang="ts">
	import { onMount } from 'svelte';
	import {
		BillingAuthError,
		billingReturnNotice,
		cancelSubscription,
		dateLabel,
		fetchSubscription,
		resumeSubscription,
		startBillingPortal,
		startSubscriptionCheckout,
		subscribeOffer,
		supporterSeatsLeft,
		withoutBillingParam,
		type Cadence,
		type BillingNotice,
		type SubscriptionState
	} from './billing';
	import { fetchPublicStats, type PublicStats } from './publicStats';

	// The account's subscription surface (maintainer steer 2026-07-21:
	// subscription only — a patronage offer, no wallet/credits UI). Reads
	// ride the same session cookie as every dashboard fetch; state *changes*
	// only ever come back through the Stripe webhook, so after a checkout
	// the panel shows honest "landing incrementally" copy instead of
	// pretending to know.

	let subscription = $state<SubscriptionState | null>(null);
	let stats = $state<PublicStats | null>(null);
	let loadError = $state<string | null>(null);
	let actionError = $state<string | null>(null);
	// One in-flight action at a time — every action here either redirects
	// the whole page or rewrites subscription state; parallel taps only
	// manufacture races with Stripe.
	let busy = $state<string | null>(null);
	let cadence = $state<Cadence>('monthly');
	let notice = $state<BillingNotice | null>(null);

	let seatsLeft = $derived(supporterSeatsLeft(stats));
	let offer = $derived(subscribeOffer(cadence, seatsLeft));
	let hasSubscription = $derived(subscription !== null && subscription.status !== null);
	let periodEndLabel = $derived(
		subscription?.current_period_end ? dateLabel(subscription.current_period_end) : null
	);

	function describeError(e: unknown, fallback: string): string {
		if (e instanceof BillingAuthError) return 'session expired — sign in again';
		return e instanceof Error && e.message ? e.message : fallback;
	}

	async function refresh() {
		try {
			subscription = await fetchSubscription();
			loadError = null;
		} catch (e) {
			loadError = describeError(e, 'billing state fetch failed');
		}
		// Decoration, not a gate — the offer renders supporter-priced when
		// the counters are absent, same as the pricing page.
		stats = await fetchPublicStats();
	}

	onMount(() => {
		notice = billingReturnNotice(window.location.search);
		if (notice) {
			// Strip the param so a reload doesn't re-announce a stale result.
			history.replaceState(history.state, '', withoutBillingParam(window.location.href));
		}
		void refresh();
	});

	async function act(name: string, run: () => Promise<void>) {
		if (busy) return;
		busy = name;
		actionError = null;
		try {
			await run();
		} catch (e) {
			actionError = describeError(e, `${name} failed`);
		} finally {
			busy = null;
		}
	}

	const subscribe = () =>
		act('subscribe', async () => {
			window.location.href = await startSubscriptionCheckout(cadence);
		});

	const openPortal = () =>
		act('portal', async () => {
			window.location.href = await startBillingPortal();
		});

	const setCancel = (cancel: boolean) =>
		act(cancel ? 'cancel' : 'resume', async () => {
			subscription = cancel ? await cancelSubscription() : await resumeSubscription();
		});
</script>

<div class="panel p-4">
	{#if notice}
		<p
			class="subpanel mb-3 px-3 py-2 font-mono text-[11px] {notice.kind === 'success'
				? 'text-amber-200'
				: 'text-ink-quiet'}"
			role="status"
		>
			{notice.text}
		</p>
	{/if}

	{#if loadError}
		<p class="text-sm text-red-400">{loadError}</p>
	{:else if subscription === null}
		<p class="text-sm text-ink-quiet">reading subscription state…</p>
	{:else}
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<p class="font-mono text-sm font-semibold text-amber-100">
				{subscription.tier}
				{#if hasSubscription && subscription.cadence}
					<span class="text-ink-quiet">· {subscription.cadence}</span>
				{/if}
			</p>
			{#if hasSubscription}
				<p class="font-mono text-[11px] text-ink-quiet">
					{#if subscription.cancel_at_period_end}
						ends {periodEndLabel ?? 'at period end'} — not renewing
					{:else if periodEndLabel}
						renews {periodEndLabel}
					{:else}
						{subscription.status}
					{/if}
				</p>
			{/if}
		</div>

		{#if !hasSubscription}
			<!-- subscribe CTA -->
			<p class="mt-2 text-sm leading-relaxed text-stone-400">
				A subscription lifts the free tier's headroom limits and funds the open-source engine. It's
				early — you'd be one of the people this gets built around.
			</p>
			<div class="mt-3 flex flex-wrap items-center gap-3">
				<div
					class="flex border border-stone-700 font-mono text-[11px] tracking-wide uppercase"
					role="group"
					aria-label="billing cadence"
				>
					{#each ['monthly', 'annual'] as const as option (option)}
						<button
							type="button"
							class="cursor-pointer px-3 py-1.5 {cadence === option
								? 'bg-amber-950/60 text-amber-200'
								: 'text-ink-quiet hover:text-stone-300'}"
							aria-pressed={cadence === option}
							onclick={() => (cadence = option)}
						>
							{option}
						</button>
					{/each}
				</div>
				<button
					type="button"
					class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70 disabled:cursor-default disabled:opacity-50"
					disabled={busy !== null}
					onclick={subscribe}
				>
					{busy === 'subscribe' ? 'starting checkout…' : `subscribe · ${offer.label}`}
				</button>
			</div>
			<p class="mt-2 font-mono text-[10px] text-ink-quiet">
				{offer.cohort === 'supporter'
					? `supporter cohort${seatsLeft !== null ? ` · ${seatsLeft} seats left` : ''} — price kept for the life of the subscription`
					: 'public pricing'}
				· Stripe-hosted checkout, price shown before you pay
			</p>
		{:else}
			<div class="mt-3 flex flex-wrap items-center gap-3">
				<button
					type="button"
					class="cursor-pointer border border-stone-700 px-3 py-1.5 font-mono text-[11px] tracking-wide text-stone-300 uppercase hover:border-stone-500 disabled:cursor-default disabled:opacity-50"
					disabled={busy !== null}
					onclick={openPortal}
				>
					{busy === 'portal' ? 'opening portal…' : 'manage · card / invoices'}
				</button>
				{#if subscription.cancel_at_period_end}
					<button
						type="button"
						class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70 disabled:cursor-default disabled:opacity-50"
						disabled={busy !== null}
						onclick={() => setCancel(false)}
					>
						{busy === 'resume' ? 'resuming…' : 'resume renewal'}
					</button>
				{:else}
					<button
						type="button"
						class="cursor-pointer border border-stone-700 px-3 py-1.5 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300 disabled:cursor-default disabled:opacity-50"
						disabled={busy !== null}
						onclick={() => setCancel(true)}
					>
						{busy === 'cancel' ? 'canceling…' : 'cancel at period end'}
					</button>
				{/if}
			</div>
			<p class="mt-2 font-mono text-[10px] text-ink-quiet">
				thank you — this funds the engine you're running, and you're early enough to shape it
			</p>
		{/if}

		{#if actionError}
			<p class="mt-3 text-sm text-red-400">{actionError}</p>
		{/if}
	{/if}
</div>
