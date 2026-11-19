<script lang="ts">
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import {
		BillingAuthError,
		billingReturnNotice,
		cancelSubscription,
		dateLabel,
		fetchSubscription,
		installSessionOffer,
		RETURN_REFETCH_MS,
		resumeSubscription,
		startBillingPortal,
		startSubscriptionCheckout,
		SUBSCRIBE_PITCH,
		subscribeOffer,
		withoutBillingParam,
		type BillingNotice,
		type SubscriptionState
	} from './billing';
	import { fetchPricing, type PriceFigure } from './pricing';
	import { BASE, getJSON } from './board/parts';

	let subscription = $state<SubscriptionState | null>(null);
	let refreshing = false;
	let loadError = $state<string | null>(null);
	let actionError = $state<string | null>(null);
	// One in-flight action at a time — every action here either redirects
	// the whole page or rewrites subscription state; parallel taps only
	// manufacture races with Stripe.
	let busy = $state<string | null>(null);
	let notice = $state<BillingNotice | null>(null);

	// The label is the Stripe Price checkout will charge (/v1/stats/pricing),
	// in its own currency; the €12 literal shows only if that read fails.
	// Until it answers the button names no price, so no figure flips under
	// the reader's eye.
	let figure = $state<PriceFigure | null | undefined>(undefined);
	let offer = $derived(subscribeOffer(figure));
	let hasSubscription = $derived(subscription !== null && subscription.status !== null);
	let installOffer = $derived(installSessionOffer(subscription));
	let boardReady = $state(false);
	let checkoutPending = $derived(notice?.kind === 'subscription' && !hasSubscription);
	let noticeText = $derived(
		notice?.kind === 'subscription' && hasSubscription ? 'Subscription confirmed.' : notice?.text
	);
	let periodEndLabel = $derived(
		subscription?.current_period_end ? dateLabel(subscription.current_period_end) : null
	);

	function describeError(e: unknown, fallback: string): string {
		if (e instanceof BillingAuthError) return 'session expired — sign in again';
		return e instanceof Error && e.message ? e.message : fallback;
	}

	async function refresh() {
		if (refreshing) return;
		refreshing = true;
		try {
			subscription = await fetchSubscription();
			loadError = null;
		} catch (e) {
			loadError = describeError(e, 'billing state fetch failed');
		} finally {
			refreshing = false;
		}
	}

	onMount(() => {
		notice = billingReturnNotice(window.location.search);
		if (notice && notice.kind !== 'subscription') {
			// Strip the param so a reload doesn't re-announce a stale result.
			history.replaceState(history.state, '', withoutBillingParam(window.location.href));
		}
		void refresh().then(() => {
			if (checkoutPending) void awaitWebhook();
		});
		void fetchPricing().then((p) => (figure = p?.public_monthly ?? null));
	});

	// The return from Stripe usually beats the webhook that records the
	// subscription; reread a few times so the payer sees it (and, for a first
	// subscriber, the install-session link) without reloading.
	$effect(() => {
		if (notice?.kind === 'subscription' && hasSubscription) {
			history.replaceState(history.state, '', withoutBillingParam(window.location.href));
		}
	});

	$effect(() => {
		if (!hasSubscription) return;
		let current = true;
		void getJSON(`${BASE}/index`)
			.then((index) => {
				if (current) boardReady = !!index && typeof index === 'object' && 'parts' in index;
			})
			.catch(() => {});
		return () => {
			current = false;
		};
	});

	async function awaitWebhook() {
		for (const ms of RETURN_REFETCH_MS) {
			if (hasSubscription) return;
			await new Promise((r) => setTimeout(r, ms));
			await refresh();
		}
	}

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
			window.location.href = await startSubscriptionCheckout(offer.cadence);
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
			class="subpanel mb-3 px-3 py-2 font-mono text-[11px] {notice.kind !== 'quiet'
				? 'text-amber-200'
				: 'text-ink-quiet'}"
			role="status"
		>
			{noticeText}
		</p>
	{/if}

	{#if loadError}
		<p class="text-sm text-red-400">{loadError}</p>
		<button type="button" class="text-sm text-amber-200 underline" onclick={refresh}
			>Try again</button
		>
	{:else if checkoutPending}
		<button type="button" class="text-sm text-amber-200 underline" onclick={refresh}>
			Check payment status
		</button>
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
				{SUBSCRIBE_PITCH} <a href={resolve('/')} class="text-amber-200 underline">Set up brnrd</a>.
			</p>
			<div class="mt-3 flex flex-wrap items-center gap-3">
				<button
					type="button"
					class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70 disabled:cursor-default disabled:opacity-50"
					disabled={busy !== null}
					onclick={subscribe}
				>
					{busy === 'subscribe'
						? 'starting checkout…'
						: figure === undefined
							? 'subscribe'
							: `subscribe · ${offer.label}`}
				</button>
			</div>
			<p class="mt-2 font-mono text-[10px] text-ink-quiet">
				Stripe-hosted checkout{figure === undefined ? '' : ` · ${offer.taxNote}`} · a founding price,
				unchanged while your subscription is live
			</p>
		{:else}
			<a
				href={resolve(boardReady ? '/' : '/account#setup')}
				class="mt-3 inline-block text-sm text-amber-200 underline"
				>{boardReady ? 'Open your board' : 'Set up brnrd'}</a
			>
			{#if installOffer}
				<p class="install-offer mt-3 text-sm text-amber-100">
					{installOffer.lead}
					<!-- eslint-disable svelte/no-navigation-without-resolve -- an external booking link -->
					<a href={installOffer.url} target="_blank" rel="noopener" class="underline">
						{installOffer.link}</a
					>.
					<!-- eslint-enable svelte/no-navigation-without-resolve -->
				</p>
			{/if}
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
