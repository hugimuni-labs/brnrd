<script lang="ts">
	import { resolve } from '$app/paths';
	import { SOCIAL_IMAGE } from '$lib/seo';

	const contactHref =
		'mailto:alexandra@hugimuni.fr?subject=Meet%20brnrd%20at%20Web%20Summit%20Lisbon%202026';
	const discountCode = 'HUGIMUNISAS_LIS26';
	let copyState = $state<'idle' | 'copied' | 'failed'>('idle');

	async function copyDiscountCode() {
		try {
			await navigator.clipboard.writeText(discountCode);
			copyState = 'copied';
		} catch {
			copyState = 'failed';
		}

		window.setTimeout(() => {
			copyState = 'idle';
		}, 1800);
	}
</script>

<svelte:head>
	<title>Meet brnrd at Web Summit Lisbon 2026</title>
	<meta
		name="description"
		content="Meet the HugiMuni team and brnrd at Web Summit Lisbon, November 9–12, 2026. Our network gets 20% off General Admission with code HUGIMUNISAS_LIS26."
	/>
	<meta property="og:title" content="Meet brnrd at Web Summit Lisbon 2026" />
	<meta
		property="og:description"
		content="We’re bringing brnrd to Lisbon. Meet us at Web Summit, November 9–12, 2026, and get 20% off General Admission with our network code."
	/>
	<meta property="og:image" content={SOCIAL_IMAGE} />
	<meta name="twitter:title" content="Meet brnrd at Web Summit Lisbon 2026" />
	<meta
		name="twitter:description"
		content="We’re bringing brnrd to Lisbon. Meet us at Web Summit, November 9–12, 2026, and get 20% off General Admission with our network code."
	/>
	<meta name="twitter:image" content={SOCIAL_IMAGE} />
</svelte:head>

<div class="mx-auto max-w-4xl p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-3xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
				HugiMuni SAS · Web Summit Lisbon
			</p>
		</div>
		<a
			href={resolve('/')}
			class="pt-2 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>home</a
		>
	</header>

	<main>
		<section class="mt-16 max-w-3xl" aria-labelledby="websummit-title">
			<p class="eyebrow">Lisbon · November 9–12, 2026</p>
			<h1
				id="websummit-title"
				class="mt-3 font-mono text-3xl font-semibold tracking-tight text-amber-100 md:text-5xl"
			>
				Meet brnrd at Web Summit Lisbon
			</h1>
			<p class="mt-6 max-w-2xl text-base leading-relaxed text-stone-300 md:text-lg">
				We’re bringing brnrd — a resident AI worker for Claude Code and Codex — to Lisbon.
			</p>
			<p class="mt-4 max-w-2xl text-sm leading-relaxed text-stone-400 md:text-base">
				If you’re building agents, developer infrastructure, AI tooling, or just want to see what
				we’re working on, we’d love to meet.
			</p>

			<div class="mt-8">
				<a
					href={contactHref}
					class="inline-flex items-center justify-center border border-amber-700 bg-amber-950/40 px-5 py-3 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				>
					meet us in Lisbon →
				</a>
			</div>
		</section>

		<section class="panel mt-14 max-w-3xl p-6 md:p-8" aria-labelledby="discount-title">
			<p class="eyebrow">going to Web Summit?</p>
			<h2
				id="discount-title"
				class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 md:text-3xl"
			>
				Our network gets 20% off General Admission.
			</h2>
			<p class="mt-4 text-sm leading-relaxed text-stone-400">
				Use this code at checkout:
			</p>
			<button
				type="button"
				onclick={copyDiscountCode}
				class="group mt-4 flex w-full items-center justify-between gap-4 overflow-x-auto border border-stone-700 bg-black/20 px-4 py-4 text-left font-mono text-lg font-semibold tracking-wide text-amber-200 transition-colors hover:border-amber-700/70 hover:bg-amber-950/20 focus-visible:border-amber-600 focus-visible:outline-none md:text-xl"
				aria-label="Copy Web Summit discount code"
			>
				<span>{discountCode}</span>
				<span
					class="shrink-0 text-[10px] font-normal tracking-wider text-ink-quiet uppercase group-hover:text-stone-300"
					aria-live="polite"
				>
					{copyState === 'copied' ? 'copied ✓' : copyState === 'failed' ? 'copy failed' : 'copy'}
				</span>
			</button>
			<p class="mt-3 text-xs leading-relaxed text-ink-quiet">
				Network code provided by Web Summit. Discount applies to General Admission tickets.
			</p>
		</section>

		<section class="mt-10 max-w-2xl">
			<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">HugiMuni SAS × brnrd</p>
			<p class="mt-2 text-sm leading-relaxed text-stone-400">
				Come say hi, show us what you’re building, or ask us why a coding agent should still be
				there tomorrow morning.
			</p>
		</section>
	</main>

	<footer class="mt-16 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			<a class="hover:text-stone-300" href={resolve('/')}>brnrd.dev</a>
			·
			<a class="hover:text-stone-300" href={contactHref}>contact us</a>
		</p>
	</footer>
</div>
