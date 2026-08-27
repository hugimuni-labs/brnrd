<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { isComplete as legalNoticeIsComplete } from '$lib/legalNotice';
	import { GITHUB_REPO, fetchPublicStats, type PublicStats } from '$lib/publicStats';
	// Ex-tax figures on this page; a French buyer pays $6.00 for the $5 tier.
	// Why the line exists and what is still open: $lib/pricing.ts.
	// The numbers themselves are Stripe-derived (#831): fetchPricing refines
	// the baked-in literals below after mount, same pattern as `stats`.
	import { TAX_NOTE, fetchPricing, formatUsd, type PricingFigures } from '$lib/pricing';

	// Pricing (#509): one click off the landing, never on it. Numbers are
	// the accepted pricing decision (decision-pricing-shape, 2026-07):
	// supporter $5/mo · $50/yr for the first cohort, then public $7/mo ·
	// $70/yr. Stripe Price objects stay authoritative at checkout — this
	// page is the offer, not the invoice. Credits/top-up framing dropped
	// (maintainer steer 2026-07-21): the subscription is patronage that
	// removes the free tier's headroom limits — no credit product exists
	// yet, so the page doesn't promise one.
	let stats = $state<PublicStats | null>(null);
	let pricing = $state<PricingFigures | null>(null);

	const legalNoticeReady = legalNoticeIsComplete();

	onMount(async () => {
		[stats, pricing] = await Promise.all([fetchPublicStats(), fetchPricing()]);
	});

	// The public count decides which Stripe cohort is still open, but it is
	// deliberately not rendered as scarcity. A near-empty cohort is product
	// state, not useful social proof; the offer names the fixed 200-person
	// boundary without turning the live subscriber count into marketing.
	let supporterOpen = $derived(
		stats === null || stats.supporter_seats_taken < stats.supporter_seats_total
	);

	// Stripe-derived, with the accepted pricing decision as the no-JS /
	// pre-refine floor (same numbers `billing.ts`'s PRICING constant names).
	let supporterMonthly = $derived(formatUsd(pricing?.supporter_monthly) ?? '$5');
	let supporterAnnual = $derived(formatUsd(pricing?.supporter_annual) ?? '$50');
	let publicMonthly = $derived(formatUsd(pricing?.public_monthly) ?? '$7');
	let publicAnnual = $derived(formatUsd(pricing?.public_annual) ?? '$70');
</script>

<svelte:head>
	<title>pricing · brnrd</title>
	<meta
		name="description"
		content="Run the open-source brnrd resident free on your hardware, or add hosted ingress, managed identity, and the brnrd.dev dashboard."
	/>
	<link rel="canonical" href="https://brnrd.dev/pricing" />
</svelte:head>

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

	<main>
		<section class="mt-10 max-w-3xl" aria-labelledby="pricing-title">
			<p class="eyebrow">pricing</p>
			<h1
				id="pricing-title"
				class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 md:text-3xl"
			>
				The engine is free. Hosted reach is optional.
			</h1>
			<p class="mt-4 max-w-2xl text-sm leading-relaxed text-stone-400">
				The resident always runs on your hardware with the agent CLI and model subscription you
				already use. Run the open-source engine yourself, or add brnrd.dev for managed ingress,
				identity, and a dashboard you can reach from anywhere.
			</p>
		</section>

		<section class="mt-10" aria-labelledby="hosted-plans-title">
			<div class="max-w-2xl">
				<h2 id="hosted-plans-title" class="eyebrow">hosted control plane</h2>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					Same local resident, two levels of hosted reach. Pay brnrd for the route around the agent
					— not for running the agent itself.
				</p>
			</div>

			<div class="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
				<article
					class="panel flex h-full flex-col p-5"
					aria-labelledby="hosted-free-title"
					data-pricing-plan="hosted-free"
				>
					<h3 id="hosted-free-title" class="eyebrow">hosted · free</h3>
					<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">$0</p>
					<p class="font-mono text-[11px] text-ink-quiet">sign in and pair</p>
					<ul class="mt-5 space-y-2 text-sm text-stone-400">
						<li>one connected repository</li>
						<li>brnrd.dev dashboard, anywhere</li>
						<li>hosted Telegram + WhatsApp ingress</li>
						<li>managed GitHub App identity + ingress</li>
					</ul>
					<div class="mt-auto pt-6">
						<a
							class="inline-flex w-full items-center justify-center border border-amber-700 bg-amber-950/40 px-3 py-2.5 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
							href={resolve('/login')}>connect one repository</a
						>
					</div>
				</article>

				<article
					class="panel flex h-full flex-col border-amber-800/60 p-5"
					aria-labelledby="subscriber-title"
					data-pricing-plan="subscriber"
				>
					<h3 id="subscriber-title" class="eyebrow">hosted · subscriber</h3>
					{#if supporterOpen}
						<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">
							{supporterMonthly}<span class="text-sm text-ink-quiet">/mo</span>
						</p>
						<p class="font-mono text-xs leading-relaxed text-ink-quiet">
							or {supporterAnnual}/yr · founding price for the first {stats?.supporter_seats_total ??
								200} subscriptions
						</p>
						<!-- Deliberately not a struck-through "was" price: nobody has ever
						     been charged {publicMonthly}, so crossing it out would borrow
						     the discount convention for a number that is a *future*
						     price for a *different* cohort, not a past one. See
						     decision-pricing-shape.md → "Early-adopter price step" and
						     subject-legal-compliance.md → "Price display". -->
						<p class="mt-1 font-mono text-[11px] leading-relaxed text-ink-quiet">
							locked while active · later {publicMonthly}/mo or {publicAnnual}/yr
						</p>
						<p class="mt-1 font-mono text-[11px] text-ink-quiet">{TAX_NOTE}</p>
					{:else}
						<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">
							{publicMonthly}<span class="text-sm text-ink-quiet">/mo</span>
						</p>
						<p class="font-mono text-xs text-ink-quiet">or {publicAnnual}/yr</p>
						<p class="mt-1 font-mono text-[11px] text-ink-quiet">{TAX_NOTE}</p>
					{/if}
					<ul class="mt-5 space-y-2 text-sm text-stone-400">
						<li>everything in hosted Free</li>
						<li>the one-repository product cap is removed</li>
						<li>free-tier hosted event limits are removed</li>
					</ul>
					<p class="mt-4 text-xs leading-relaxed text-ink-quiet">
						Those limit lifts are live now. brnrd.dev is still early; subscribing also funds the
						open-source engine and helps shape what comes next.
					</p>
					<div class="mt-auto pt-6">
						<a
							class="inline-flex w-full items-center justify-center border border-amber-700 bg-amber-950/40 px-3 py-2.5 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
							href={resolve('/login')}
							>subscribe · {supporterOpen ? `${supporterMonthly}/mo` : `${publicMonthly}/mo`}</a
						>
					</div>
				</article>
			</div>
		</section>

		<section class="panel mt-5 p-5" aria-labelledby="self-host-title">
			<div class="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
				<div class="md:max-w-xl">
					<h2 id="self-host-title" class="eyebrow">self-host</h2>
					<div class="mt-2 flex items-baseline gap-2 font-mono">
						<p class="text-2xl font-semibold text-amber-100">$0</p>
						<p class="text-[11px] text-ink-quiet">forever</p>
					</div>
					<p class="mt-3 text-sm leading-relaxed text-stone-400">
						The full open-source engine, your gates and credentials, your infrastructure, and no
						brnrd account. Self-hosting is a deployment path — not a smaller hosted plan.
					</p>
				</div>
				<a
					class="inline-flex shrink-0 items-center justify-center border border-stone-700 px-5 py-2.5 font-mono text-[12px] tracking-wide text-stone-300 uppercase hover:border-stone-500"
					href={`https://github.com/${GITHUB_REPO}`}
					rel="external">install brnrd</a
				>
			</div>
		</section>

		<section class="mt-10 max-w-2xl" aria-labelledby="support-title">
			<h2 id="support-title" class="eyebrow">support the commons</h2>
			<p class="mt-2 text-sm leading-relaxed text-stone-400">
				For people who want to fund the project beyond a subscription, the lifetime contributor
				bundle begins at $500. It includes lifetime subscriber access and an optional permanent
				acknowledgement on the contributors page. It is arranged directly while brnrd is this early
				—
				<a class="text-sky-400 underline" href="mailto:alexandra@hugimuni.fr"
					>contact the founders privately</a
				>.
			</p>
		</section>
	</main>

	<footer class="mt-14 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			prices at checkout are set by Stripe and shown before you pay ·
			<a class="hover:text-stone-300" href={resolve('/terms')}>terms</a>
			·
			<a class="hover:text-stone-300" href={resolve('/privacy')}>privacy</a>
			<!-- Same gate as the landing footer: linked only once the notice
			     actually identifies the publisher (see $lib/legalNotice). -->
			{#if legalNoticeReady}
				· <a class="hover:text-stone-300" href={resolve('/legal-notice')}>mentions légales</a>
			{/if}
		</p>
	</footer>
</div>
