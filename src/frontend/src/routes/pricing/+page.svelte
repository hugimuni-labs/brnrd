<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { isComplete as legalNoticeIsComplete } from '$lib/legalNotice';
	import { INSTALL_LINE } from '$lib/install';
	// VAT-inclusive EUR figures on this page. Why the note exists and what it
	// must track: $lib/pricing.ts. The numbers themselves are Stripe-derived
	// (#831): fetchPricing refines the baked-in literal below after mount.
	import { TAX_NOTE, fetchPricing, formatEur, type PricingFigures } from '$lib/pricing';

	// Pricing (his go 10242, 2026-11-05; brnrd-self threads/pricing/design.md):
	// price what costs us, the deployment; never the trust product or the
	// user's fuel. Two tiers: the whole local product free, reach at €12/mo
	// per person, a founding price locked for a live subscription. Stripe
	// Price objects stay authoritative at checkout — this page is the offer,
	// not the invoice.
	let pricing = $state<PricingFigures | null>(null);

	const legalNoticeReady = legalNoticeIsComplete();

	onMount(async () => {
		pricing = await fetchPricing();
	});

	// Stripe-derived, with the accepted decision as the no-JS / pre-refine
	// floor. A Price still in USD (before his Stripe step) renders the floor,
	// never a dollar figure on a euro page.
	let premiumMonthly = $derived(formatEur(pricing?.public_monthly) ?? '€12');
</script>

<svelte:head>
	<title>pricing · brnrd</title>
	<meta
		name="description"
		content="The whole brnrd resident is free on your machine, with every guard and receipt. Premium is reach: brnrd.dev, Telegram and the GitHub App, at €12 a month."
	/>
	<meta property="og:title" content="pricing · brnrd" />
	<meta
		property="og:description"
		content="The whole brnrd resident is free on your machine, with every guard and receipt. Premium is reach: brnrd.dev, Telegram and the GitHub App, at €12 a month."
	/>
	<meta name="twitter:title" content="pricing · brnrd" />
	<meta
		name="twitter:description"
		content="The whole brnrd resident is free on your machine, with every guard and receipt. Premium is reach: brnrd.dev, Telegram and the GitHub App, at €12 a month."
	/>
	<!-- canonical + og:url come from +layout.svelte (hasCanonicalMeta) -->
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
				The resident is free. Reach is premium.
			</h1>
			<p class="mt-4 max-w-2xl text-sm leading-relaxed text-stone-400">
				brnrd runs on your machine with the agent CLI and model subscription you already pay for. We
				charge for what costs us money, the hosted reach, and never for your tokens, your projects,
				or the guards that make it safe to hand work over.
			</p>
		</section>

		<section class="mt-10" aria-labelledby="plans-title">
			<h2 id="plans-title" class="sr-only">plans</h2>
			<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
				<article
					class="panel flex h-full flex-col p-5"
					aria-labelledby="free-title"
					data-pricing-plan="free"
				>
					<h3 id="free-title" class="eyebrow">free</h3>
					<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">€0</p>
					<p class="font-mono text-[11px] text-ink-quiet">the whole local product · one person</p>
					<ul class="mt-5 space-y-2 text-sm text-stone-400">
						<li>the loom on your machine, on your own fuel</li>
						<li>your own GitHub, unlimited repositories and threads</li>
						<li>the local board</li>
						<li>every guard and every receipt</li>
					</ul>
					<div class="mt-auto pt-6">
						<pre
							class="panel overflow-x-auto px-3 py-2 font-mono text-[12px] whitespace-pre-wrap text-stone-200"
							aria-label="install brnrd"><code
								><span class="text-ink-mute select-none">$ </span>{INSTALL_LINE}</code
							></pre>
					</div>
				</article>

				<article
					class="panel flex h-full flex-col border-amber-800/60 p-5"
					aria-labelledby="premium-title"
					data-pricing-plan="premium"
				>
					<h3 id="premium-title" class="eyebrow">premium</h3>
					<p class="mt-2 font-mono text-2xl font-semibold text-amber-100">
						{premiumMonthly}<span class="text-sm text-ink-quiet">/mo per person</span>
					</p>
					<p class="mt-1 font-mono text-[11px] text-ink-quiet">{TAX_NOTE}</p>
					<p class="mt-1 font-mono text-[11px] text-amber-200/80">
						founding price · locked: no price change for a live subscription
					</p>
					<ul class="mt-5 space-y-2 text-sm text-stone-400">
						<li>everything in free</li>
						<li>brnrd.dev: your board from anywhere, phone included</li>
						<li>the Telegram bot, through the hosted relay</li>
						<li>the managed GitHub App identity</li>
					</ul>
					<p class="mt-4 text-xs leading-relaxed text-ink-quiet">
						One repository stays free on brnrd.dev while brnrd is early. Cancel and the founding
						price goes with the subscription.
					</p>
					<div class="mt-auto pt-6">
						<a
							class="inline-flex w-full items-center justify-center border border-amber-700 bg-amber-950/40 px-3 py-2.5 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
							href={resolve('/login')}>subscribe · {premiumMonthly}/mo</a
						>
					</div>
				</article>
			</div>
		</section>

		<section class="mt-10 max-w-2xl" aria-labelledby="never-title">
			<h2 id="never-title" class="eyebrow">never priced</h2>
			<p class="mt-2 text-sm leading-relaxed text-stone-400">
				Tokens, projects, threads, and the size of your resident's memory. Your fuel is yours: you
				buy it from your model vendor, and brnrd never meters it.
			</p>
		</section>
	</main>

	<footer class="mt-14 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			prices at checkout are set by Stripe and shown, VAT included, before you pay ·
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
