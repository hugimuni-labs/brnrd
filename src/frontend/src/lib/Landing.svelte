<script lang="ts">
	import { onMount } from 'svelte';
	import {
		GITHUB_REPO,
		fetchPublicStats,
		fetchRepoStats,
		type PublicStats,
		type RepoStats
	} from '$lib/publicStats';
	import { typeReveal } from '$lib/transitions';

	// The landing (#509): what an anonymous visitor sees at brnrd.dev.
	// Two doors, one truth — in both of them the agent executes on the
	// visitor's own machine. The managed side is a control plane, not a
	// compute farm, and the copy must never imply otherwise (the
	// monetization survey's corrective, 2026-07-20).
	let stats = $state<PublicStats | null>(null);
	let repo = $state<RepoStats | null>(null);
	let countersLoaded = $state(false);

	onMount(async () => {
		// One shot each, no polling — counters are proof of life, not telemetry.
		const [s, r] = await Promise.all([fetchPublicStats(), fetchRepoStats()]);
		stats = s;
		repo = r;
		countersLoaded = true;
	});

	let seatsLeft = $derived(
		stats === null
			? null
			: Math.max(0, stats.supporter_seats_total - stats.supporter_seats_taken)
	);
</script>

<div class="mx-auto max-w-4xl p-6">
	<header class="ignite flex items-start justify-between gap-4" style="--ignite-delay: 0ms">
		<div>
			<p class="font-mono text-3xl font-semibold tracking-tight text-amber-100">brnrd</p>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
				drain local · route wisely
			</p>
		</div>
		<nav class="flex items-center gap-4 pt-2">
			<a
				href="https://gurio.github.io/brr/"
				rel="external"
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>docs</a
			>
			<a
				href={`https://github.com/${GITHUB_REPO}`}
				rel="external"
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>github</a
			>
			<a
				href="/pricing"
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>pricing</a
			>
			<a
				href="/login"
				class="border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				>sign in</a
			>
		</nav>
	</header>

	<section class="ignite mt-12 max-w-2xl" style="--ignite-delay: 160ms" aria-label="what brnrd is">
		<h1
			class="font-mono text-xl font-semibold tracking-tight text-amber-100"
			use:typeReveal={{ text: 'a resident, not a chatbot', delay: 200 }}
		>
			a resident, not a chatbot
		</h1>
		<p class="mt-4 text-sm leading-relaxed text-stone-400">
			brnrd runs resident coding agents that live with your repositories. Work arrives from
			GitHub issues, review requests, and Telegram messages; a daemon on your own machine turns
			it into runs; the results come back as commits, pull requests, and replies on the thread
			that asked. The resident keeps memory between runs — decisions, pitfalls, a knowledge
			base — so it gets better at your project instead of starting over.
		</p>
		<p class="mt-3 text-sm leading-relaxed text-stone-400">
			Your models, your keys, your hardware. brnrd drives the coding CLIs you already
			subscribe to — Claude Code, Codex, and friends — and paces itself against your quotas.
		</p>
	</section>

	<section class="ignite mt-10" style="--ignite-delay: 300ms" aria-label="two ways to run brnrd">
		<p class="eyebrow">two doors, same engine</p>
		<div class="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
			<div class="panel p-4">
				<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">self-host</p>
				<h2 class="mt-1 font-mono text-lg font-semibold tracking-tight text-amber-100">
					free forever
				</h2>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					The full engine is open source. Install it, point it at a checkout, wire your own
					gates, keep every byte on machines you control. No account, no phone-home, no
					feature gate.
				</p>
				<a
					class="mt-4 inline-flex items-center gap-2 border border-stone-700 px-3 py-2 font-mono text-[12px] tracking-wide text-stone-300 uppercase hover:border-stone-500"
					href={`https://github.com/${GITHUB_REPO}`}
					rel="external">read the source</a
				>
			</div>
			<div class="panel p-4">
				<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">managed</p>
				<h2 class="mt-1 font-mono text-lg font-semibold tracking-tight text-amber-100">
					brnrd.dev control plane
				</h2>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					Sign in with GitHub, pair your daemon in minutes: hosted Telegram and GitHub
					ingress, this dashboard from anywhere, a managed GitHub App identity for the
					resident's pushes and replies.
				</p>
				<p class="mt-2 text-xs leading-relaxed text-ink-quiet">
					Execution stays on your machine — brnrd.dev is the control plane, not a compute
					farm.
				</p>
				<a
					class="mt-4 inline-flex items-center gap-2 border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					href="/login">sign in with GitHub</a
				>
			</div>
		</div>
	</section>

	<!-- Counters vanish rather than apologize: when both sources fail, an
	     empty "alive" section or a stuck "counting…" would each claim
	     something the page can't back. -->
	{#if !countersLoaded || stats !== null || repo !== null}
	<section class="ignite mt-10" style="--ignite-delay: 450ms" aria-label="live counters">
		<p class="eyebrow">alive, in numbers</p>
		<p class="mt-2 font-mono text-sm text-stone-400">
			{#if stats !== null}
				<span class="text-amber-100">{stats.accounts}</span> accounts
			{/if}
			{#if repo !== null}
				{#if stats !== null}·{/if}
				<span class="text-amber-100">★ {repo.stars}</span> stars ·
				<span class="text-amber-100">{repo.forks}</span> forks
			{/if}
			{#if seatsLeft !== null && seatsLeft > 0}
				· <span class="text-amber-100">{seatsLeft}</span> of
				{stats?.supporter_seats_total} supporter seats left
			{/if}
			{#if !countersLoaded && stats === null && repo === null}
				<span class="text-ink-quiet">counting…</span>
			{/if}
		</p>
		{#if seatsLeft !== null && seatsLeft > 0}
			<p class="mt-1 text-xs text-ink-quiet">
				The supporter cohort keeps its price for the life of the subscription —
				<a class="text-sky-400 underline" href="/pricing">details on pricing</a>.
			</p>
		{/if}
	</section>
	{/if}

	<footer class="ignite mt-14 border-t border-stone-800 pt-4" style="--ignite-delay: 600ms">
		<p class="font-mono text-[10px] text-ink-mute">
			open source · runs on your hardware ·
			<a class="hover:text-stone-300" href="/terms">terms</a>
			·
			<a class="hover:text-stone-300" href={`https://github.com/${GITHUB_REPO}/blob/main/SECURITY.md`} rel="external"
				>security</a
			>
		</p>
	</footer>
</div>
