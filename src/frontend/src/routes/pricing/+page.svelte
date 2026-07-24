<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { GITHUB_REPO, fetchPublicStats, type PublicStats } from '$lib/publicStats';

	// Pricing (#509): one click off the landing, never on it. Numbers are
	// the accepted pricing decision (decision-pricing-shape, 2026-07):
	// supporter $5/mo · $50/yr for the first cohort, then public $7/mo ·
	// $70/yr. Stripe Price objects stay authoritative at checkout — this
	// page is the offer, not the invoice. Credits/top-up framing dropped
	// (maintainer steer 2026-07-21): the subscription is patronage that
	// removes the free tier's headroom limits — no credit product exists
	// yet, so the page doesn't promise one.
	let stats = $state<PublicStats | null>(null);

	onMount(async () => {
		stats = await fetchPublicStats();
	});

	let seatsLeft = $derived(
		stats === null ? null : Math.max(0, stats.supporter_seats_total - stats.supporter_seats_taken)
	);
	let supporterOpen = $derived(seatsLeft === null || seatsLeft > 0);
</script>

<svelte:head><title>pricing · brnrd</title></svelte:head>

<div class="mx-auto max-w-4xl p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-3xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">pricing</p>
		</div>
		<nav class="flex items-center gap-4 pt-2">
			<a
				href={resolve('/')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>home</a
			>
			<a
				href={resolve('/login')}
				class="border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				>sign in</a
			>
		</nav>
	</header>

	<p class="mt-8 max-w-2xl text-sm leading-relaxed text-stone-400">
		In every tier the agent runs on your hardware with your model subscriptions. Paying for brnrd
		pays for the hosted control plane — ingress, dashboard, managed GitHub identity — and for the
		project existing at all.
	</p>

	<div class="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
		<section class="panel p-4" aria-label="self-host tier">
			<p class="eyebrow">self-host</p>
			<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">$0</p>
			<p class="font-mono text-[11px] text-ink-quiet">forever</p>
			<ul class="mt-4 space-y-2 text-sm text-stone-400">
				<li>the whole engine, open source</li>
				<li>your gates, your tokens, your infrastructure</li>
				<li>no account at all</li>
			</ul>
			<a
				class="mt-5 inline-flex w-full items-center justify-center border border-stone-700 px-3 py-2 font-mono text-[12px] tracking-wide text-stone-300 uppercase hover:border-stone-500"
				href={`https://github.com/${GITHUB_REPO}`}
				rel="external">get the source</a
			>
		</section>

		<section class="panel p-4" aria-label="hosted free tier">
			<p class="eyebrow">hosted · free</p>
			<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">$0</p>
			<p class="font-mono text-[11px] text-ink-quiet">sign in and pair</p>
			<ul class="mt-4 space-y-2 text-sm text-stone-400">
				<li>brnrd.dev dashboard, anywhere</li>
				<li>hosted Telegram + GitHub ingress</li>
				<li>managed GitHub App identity for the resident</li>
			</ul>
			<a
				class="mt-5 inline-flex w-full items-center justify-center border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				href={resolve('/login')}>start free</a
			>
		</section>

		<section class="panel border-amber-800/60 p-4" aria-label="subscriber tier">
			{#if supporterOpen}
				<p class="eyebrow">subscriber · supporter cohort</p>
				<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">
					$5<span class="text-sm text-ink-quiet">/mo</span>
				</p>
				<p class="font-mono text-[11px] text-ink-quiet">
					or $50/yr · first {stats?.supporter_seats_total ?? 200} accounts, price kept for the life of
					the subscription
					{#if seatsLeft !== null}
						· {seatsLeft} left
					{/if}
				</p>
			{:else}
				<p class="eyebrow">subscriber</p>
				<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">
					$7<span class="text-sm text-ink-quiet">/mo</span>
				</p>
				<p class="font-mono text-[11px] text-ink-quiet">or $70/yr</p>
			{/if}
			<ul class="mt-4 space-y-2 text-sm text-stone-400">
				<li>everything in hosted free</li>
				<li>headroom limits: off — the resident works as hard as you ask</li>
				<li>funds the open-source engine it runs on</li>
			</ul>
			<a
				class="mt-5 inline-flex w-full items-center justify-center border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				href={resolve('/login')}>sign in to subscribe</a
			>
			<p class="mt-3 text-xs leading-relaxed text-ink-quiet">
				Early access: checkout is Stripe-hosted and live; entitlements are still landing. You'd be
				backing the build while it's early — which is exactly when backing shapes it. Priced for the
				people who show up first.
			</p>
		</section>
	</div>

	<section class="mt-8 max-w-2xl" aria-label="contributor bundle">
		<p class="eyebrow">premium contributor bundle</p>
		<p class="mt-2 text-sm leading-relaxed text-stone-400">
			A lifetime package at $500+: lifetime headroom-free access, a line on the contributors page —
			nickname and pledge each optionally redacted — and a permanent place on the leaderboard. No
			self-serve checkout yet: open an issue or reach the maintainers
			<a
				class="text-sky-400 underline"
				href={`https://github.com/${GITHUB_REPO}/issues`}
				rel="external">on GitHub</a
			> and it will be arranged by hand, which at this stage is the honest interface.
		</p>
	</section>

	<footer class="mt-14 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			prices at checkout are set by Stripe and shown before you pay ·
			<a class="hover:text-stone-300" href={resolve('/terms')}>terms</a>
			·
			<a class="hover:text-stone-300" href={resolve('/privacy')}>privacy</a>
		</p>
	</footer>
</div>
