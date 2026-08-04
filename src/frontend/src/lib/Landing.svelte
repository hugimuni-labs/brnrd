<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		DOCS_URL,
		GITHUB_REPO,
		fetchPublicStats,
		fetchRepoStats,
		type PublicStats,
		type RepoStats
	} from '$lib/publicStats';
	import { isComplete as legalNoticeIsComplete } from '$lib/legalNotice';
	import { typeReveal } from '$lib/transitions';
	import WinkWordmark from '$lib/WinkWordmark.svelte';
	import HeroExchange from '$lib/HeroExchange.svelte';
	import ShelfIcon from '$lib/ShelfIcon.svelte';
	import { SHELLS, doorRows, fetchDoorStatus, type DoorStatus } from '$lib/supportMatrix';

	// The landing (#509): what an anonymous visitor sees at brnrd.dev.
	// Two doors, one truth — in both of them the agent executes on the
	// visitor's own machine. The managed side is a control plane, not a
	// compute farm, and the copy must never imply otherwise (the
	// monetization survey's corrective, 2026-07-20).
	let stats = $state<PublicStats | null>(null);
	let repo = $state<RepoStats | null>(null);
	let countersLoaded = $state(false);
	// null = no confirmed door status yet (pre-fetch or the fetch failed) —
	// doorRows() renders every door in that state as `status: null`, never
	// as a guessed `live` (see supportMatrix.ts).
	let doorStatuses = $state<Map<string, DoorStatus> | null>(null);

	const legalNoticeReady = legalNoticeIsComplete();

	onMount(async () => {
		// One shot each, no polling — counters are proof of life, not telemetry.
		const [s, r, doors] = await Promise.all([
			fetchPublicStats(),
			fetchRepoStats(),
			fetchDoorStatus()
		]);
		stats = s;
		repo = r;
		countersLoaded = true;
		doorStatuses = doors;
	});

	let seatsLeft = $derived(
		stats === null ? null : Math.max(0, stats.supporter_seats_total - stats.supporter_seats_taken)
	);
	let doors = $derived(doorRows(doorStatuses));
</script>

<div class="mx-auto max-w-4xl p-6">
	<header class="ignite flex items-start justify-between gap-4" style="--ignite-delay: 0ms">
		<div>
			<p class="font-mono text-3xl font-semibold tracking-tight text-amber-100">
				<WinkWordmark />
			</p>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
				drain local · route wisely
			</p>
		</div>
		<nav class="flex items-center gap-4 pt-2">
			<a
				href={DOCS_URL}
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
				href={resolve('/pricing')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>pricing</a
			>
			<a
				href={resolve('/login')}
				class="border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				>sign in</a
			>
		</nav>
	</header>

	<section class="ignite mt-12" style="--ignite-delay: 160ms" aria-label="what brnrd is">
		<!-- The headline and the bodies sit above the split so that on a phone —
		     where the two columns stack — the exchange lands directly under them
		     instead of under four paragraphs of prose. Seen live 2026-07-31 on
		     the maintainer's phone: the first screen of brnrd.dev was all telling
		     and no showing, and the one thing the genre research says makes a
		     peer landing "hit home immediately" is that it *shows* how it is
		     used. So: headline, what it drives, the exchange — then the argument,
		     for whoever is still reading. -->
		<div class="max-w-2xl">
			<h1
				class="font-mono text-xl font-semibold tracking-tight text-amber-100"
				use:typeReveal={{ text: 'a resident, not a chatbot', delay: 200 }}
			>
				a resident, not a chatbot
			</h1>
			<!-- The bodies belong on the fold, not in paragraph two (2026-07-29):
			     "does it drive the thing I already pay for" is a one-glance
			     question, and making a visitor read for it loses the ones who
			     don't. Named CLIs only — "more to come" is a roadmap, not a
			     claim, so it must never read as a list of what works today. -->
			<p class="mt-2 font-mono text-[13px] leading-relaxed text-amber-200/80">
				runs on Claude Code and Codex — more to come
			</p>
		</div>
		<div class="mt-6 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
			<div class="order-2 max-w-2xl lg:order-1">
				<p class="text-sm leading-relaxed text-stone-400">
					brnrd runs resident coding agents that live with your repositories. Work arrives from
					GitHub issues, review requests, and Telegram messages; a daemon on your own machine turns
					it into runs; the results come back as commits, pull requests, and replies on the thread
					that asked. The resident keeps memory between runs — decisions, pitfalls, a knowledge base
					— so it gets better at your project instead of starting over.
				</p>
				<!-- Monetization survey corrective, reiterated at the hero (2026-07-20):
				     self-hosted is the base product, not a trial of the managed one. No
				     GUI to open, no account to make first — the trigger is the CLI
				     already on your machine and the message you send it. -->
				<p class="mt-3 text-sm leading-relaxed text-stone-400">
					Your models, your keys, your hardware, your agent CLI — that's the whole product,
					self-hosted and free. No account, no payment, no feature gate. Sign in only for
					brnrd.dev's convenience layer on top: hosted gates so you can message it from anywhere,
					and this dashboard.
				</p>
				<p class="mt-3 text-xs leading-relaxed text-ink-quiet">
					It runs on your machine. Your repo never leaves it — connecting an account mirrors derived
					project notes to brnrd.dev, never your source tree.
				</p>
			</div>
			<div class="order-1 lg:order-2">
				<HeroExchange />
			</div>
		</div>
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
					The full engine is open source. Install it, point it at a checkout, wire your own gates,
					keep every byte on machines you control. No account, no phone-home, no feature gate.
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
					Sign in with GitHub, pair your daemon in minutes: hosted Telegram and GitHub ingress, this
					dashboard from anywhere, a managed GitHub App identity for the resident's pushes and
					replies.
				</p>
				<p class="mt-2 text-xs leading-relaxed text-ink-quiet">
					Execution stays on your machine — brnrd.dev is the control plane, not a compute farm.
				</p>
				<a
					class="mt-4 inline-flex items-center gap-2 border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					href={resolve('/login')}>sign in with GitHub</a
				>
			</div>
		</div>
	</section>

	<!-- The shells-and-doors shelf (#1070's docs-page block, brought to this
	     landing). Two different truths on purpose: Shells is static prose
	     data (both bundled CLIs are always live — see supportMatrix.ts for
	     why deriving two names from `_BUNDLED_CORES` live isn't worth a
	     fetch + loading state); Doors is never hardcoded live/soon — that's
	     exactly what went stale on the docs page in one day (#1072, #1074).
	     A door with no confirmed status yet reads "checking…", never a
	     guessed "live" — the failure #964/#1070's own soon-tags hazard was:
	     silence reading as a claim of completeness. -->
	<section class="ignite mt-10" style="--ignite-delay: 380ms" aria-label="shells and doors">
		<p class="eyebrow">shells and doors</p>
		<div class="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
			<div class="panel p-4">
				<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">shells</p>
				<ul class="mt-3 flex flex-col gap-2.5">
					{#each SHELLS as shell (shell.slug)}
						<li class="flex items-center gap-2.5">
							<ShelfIcon icon={shell.icon} />
							<span class="text-sm text-stone-300">{shell.label}</span>
						</li>
					{/each}
				</ul>
			</div>
			<div class="panel p-4">
				<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">doors</p>
				<ul class="mt-3 flex flex-col gap-2.5">
					{#each doors as door (door.slug)}
						<li class="flex items-center gap-2.5">
							<ShelfIcon icon={door.icon} />
							<span class="text-sm text-stone-300">{door.label}</span>
							<span class="ml-auto flex shrink-0 items-center gap-1.5">
								{#if door.tag}
									<span
										class="border border-stone-700 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
										>{door.tag}</span
									>
								{/if}
								{#if door.status === 'soon'}
									<span
										class="border border-amber-700/50 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-amber-300 uppercase"
										>soon</span
									>
								{:else if door.status === null}
									<span
										class="border border-stone-800 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
										>checking…</span
									>
								{/if}
							</span>
						</li>
					{/each}
				</ul>
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
				<!-- A zero counter is anti-proof on a section arguing liveness —
			     zeros hide themselves (seen live 2026-07-20: "★ 0 stars ·
			     0 forks" pre-move, pre-announcement). -->
				{#if repo !== null && repo.stars > 0}
					{#if stats !== null}·{/if}
					<span class="text-amber-100">★ {repo.stars}</span> stars
					{#if repo.forks > 0}
						· <span class="text-amber-100">{repo.forks}</span> forks
					{/if}
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
					<a class="text-sky-400 underline" href={resolve('/pricing')}>details on pricing</a>.
				</p>
			{/if}
		</section>
	{/if}

	<footer class="ignite mt-14 border-t border-stone-800 pt-4" style="--ignite-delay: 600ms">
		<p class="font-mono text-[10px] text-ink-mute">
			open source · runs on your hardware ·
			<a class="hover:text-stone-300" href={resolve('/terms')}>terms</a>
			·
			<a class="hover:text-stone-300" href={resolve('/privacy')}>privacy</a>
			·
			<!-- The link is gated on the notice being complete, from the same
			     registry the page renders ($lib/legalNotice): pointing the public
			     at a mentions légales that still shows ⟨à compléter⟩ would be a
			     worse claim than not linking it. Fill the K-bis values and the
			     link appears — nothing to remember here. -->
			{#if legalNoticeReady}
				<a class="hover:text-stone-300" href={resolve('/legal-notice')}>mentions légales</a>
				·
			{/if}
			<a
				class="hover:text-stone-300"
				href={`https://github.com/${GITHUB_REPO}/blob/main/SECURITY.md`}
				rel="external">security</a
			>
		</p>
	</footer>
</div>
