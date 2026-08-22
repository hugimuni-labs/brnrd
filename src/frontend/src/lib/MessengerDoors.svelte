<script lang="ts">
	// brr/every-door-on-the-page — the persistent, account-level pairing
	// surface: every messenger door the registry (`brnrd.messenger_doors`)
	// declares, lit or not, always rendered — never gated to onboarding
	// state or mobile like `ColdStart.svelte`'s cold-start CTA is. That
	// component answers "how do I get my first chat paired, from a phone,
	// before anything else is set up"; this one answers "I already have an
	// account — connect (or re-mint) any door, any time," and it's the
	// surface the maintainer's own ask named directly: "we should find a
	// place to initiate the pairing here (or on the main page)."
	//
	// Placed on `/repos` (see `routes/repos/+page.svelte`), argued against
	// the main page: the main page's `ColdStart` block is an onboarding
	// checklist that deliberately narrows and then disappears once a repo
	// is enabled and a daemon paired (its whole design history, in its own
	// doc comment, is fighting *against* showing settled users a wall of
	// setup chrome) — bolting a permanent mint control onto it either
	// resurrects that wall or means the control vanishes for exactly the
	// account that has been through onboarding once and wants to add a
	// second chat platform later. `/repos` is already titled "repository
	// control", already carries the account-level `PairedChats` panel this
	// one sits beside, and its own subtitle already promises "route
	// Telegram chats into brnrd" — this is that promise, generalized to
	// every door and given a mint control, not a new page.
	//
	// The maintainer's mid-run steer (2026-08-19) raised the bar on two
	// specific things, both encoded below rather than left to CSS
	// afterthought: a dark door gets its own deliberate treatment (a
	// square, unglowed, un-animated marker — never a dimmed copy of a lit
	// door's round glowing one, which reads as broken rather than
	// intentional) and the countdown is a first-class visual element with
	// three distinct, named states (`messengerDoors.ts`'s `countdown`),
	// not a number in parentheses.
	import { onDestroy, untrack } from 'svelte';
	import { DOCS_URL } from './publicStats';
	import { fetchPairStatus, mintAccountMessengerPair } from './repos';
	import type { MessengerDoor, MessengerPairStarted } from './repos';
	import {
		countdown,
		conversationLink,
		doorLabel,
		doorOffCopy,
		doorOffHasEnablePath,
		orderedDoors
	} from './messengerDoors';
	import { STATUS_GOOD, STATUS_SPENT, STATUS_WARN, statusDotStyle } from './statusPalette';

	interface Props {
		// `null` = the repos fetch hasn't landed yet — render nothing
		// rather than flashing an empty panel, same "don't invent a second
		// notion of unknown" contract every other prop on this page's
		// sibling components already follows.
		doors: MessengerDoor[] | null;
		// Test/SSR-injectable clock, same idiom `ColdStart.svelte`'s
		// `mobileOverride` already uses: `svelte/server` never runs
		// `$effect`, so a live `setInterval` can't tick there. `null` (the
		// runtime default) reads the real clock; a test pins one instant
		// to assert a specific countdown tier without waiting on a timer.
		nowOverride?: number | null;
		excludePlatforms?: string[];
		heading?: string;
		embedded?: boolean;
	}

	let {
		doors,
		nowOverride = null,
		excludePlatforms = [],
		heading = 'chat connectors',
		embedded = false
	}: Props = $props();

	const allDoors = $derived(
		orderedDoors(doors ?? []).filter(
			(door) => door.reason !== 'not_built' && !excludePlatforms.includes(door.platform)
		)
	);

	// `untrack` — this is a deliberate one-shot capture (the seed for a
	// value the interval below mutates directly), not a reactive read of
	// `nowOverride`; without it svelte-check reads the bare reference as
	// the common "only captures the initial value" mistake.
	let nowMs = $state(untrack(() => nowOverride ?? Date.now()));
	$effect(() => {
		if (nowOverride !== null) return;
		const id = setInterval(() => (nowMs = Date.now()), 1000);
		return () => clearInterval(id);
	});

	let mintingPlatform = $state<string | null>(null);
	let mintOutcomes = $state<Record<string, MessengerPairStarted>>({});
	// The TTL each `mintOutcomes` entry was actually minted with, captured
	// at mint time from the response itself — never a hardcoded 180s, so
	// the countdown's ample/low boundary tracks whatever this deployment's
	// `messenger_pair_ttl_s` really is.
	let mintTtlSeconds = $state<Record<string, number>>({});
	let mintFailedPlatforms = $state<Record<string, boolean>>({});
	let pairedOutcomes = $state<Record<string, { display: string | null }>>({});

	const pollTimers: Record<string, ReturnType<typeof setTimeout>> = {};
	const pollDeadlines: Record<string, number> = {};

	function stopPolling(platform: string) {
		clearTimeout(pollTimers[platform]);
		delete pollTimers[platform];
	}
	function stopAllPolling() {
		for (const platform of Object.keys(pollTimers)) stopPolling(platform);
	}
	onDestroy(stopAllPolling);

	async function pollPairStatus(platform: string, code: string) {
		if (Date.now() > pollDeadlines[platform]) {
			stopPolling(platform);
			return;
		}
		try {
			const status = await fetchPairStatus(code);
			if (status.consumed) {
				pairedOutcomes = { ...pairedOutcomes, [platform]: { display: status.display } };
				stopPolling(platform);
				return;
			}
		} catch {
			// Transient — keep trying until the deadline.
		}
		pollTimers[platform] = setTimeout(() => pollPairStatus(platform, code), 3000);
	}

	// Mint on tap — first mint or a deliberate remint, same call either
	// way (decision: reminting is a first-class control, not an error
	// path — it sits beside the live link, before it expires, not just
	// after). A remint mid-flight simply replaces the outcome the
	// countdown reads; the old code is left to expire server-side, same as
	// tapping a messenger door twice in `ColdStart.svelte` already does.
	async function mint(platform: string) {
		mintingPlatform = platform;
		mintFailedPlatforms = { ...mintFailedPlatforms, [platform]: false };
		pairedOutcomes = Object.fromEntries(
			Object.entries(pairedOutcomes).filter(([p]) => p !== platform)
		);
		try {
			const started = await mintAccountMessengerPair(platform);
			const ttl = Math.max(
				1,
				Math.round((new Date(started.expires_at).getTime() - Date.now()) / 1000)
			);
			mintOutcomes = { ...mintOutcomes, [platform]: started };
			mintTtlSeconds = { ...mintTtlSeconds, [platform]: ttl };
			pollDeadlines[platform] = new Date(started.expires_at).getTime() + 30_000;
			stopPolling(platform);
			pollPairStatus(platform, started.pair_code);
		} catch {
			mintFailedPlatforms = { ...mintFailedPlatforms, [platform]: true };
		} finally {
			mintingPlatform = null;
		}
	}

	function tierColor(tier: 'ample' | 'low' | 'critical'): string {
		if (tier === 'critical') return STATUS_SPENT;
		if (tier === 'low') return STATUS_WARN;
		return STATUS_GOOD;
	}
</script>

{#if allDoors.length > 0}
	<section class={embedded ? 'mt-4' : 'panel mt-6 p-4'} aria-labelledby="messenger-doors-heading">
		<p class="eyebrow">messenger doors</p>
		<h2
			id="messenger-doors-heading"
			class="font-mono text-lg font-semibold tracking-tight text-amber-100"
		>
			{heading}
		</h2>
		<p class="mt-1 max-w-2xl text-sm text-ink-quiet">
			Connect another chat to this account. Each link lives for a few minutes; re-mint it any time.
		</p>

		<div class="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
			{#each allDoors as door (door.platform)}
				<div class="subpanel p-3" data-testid={`door-${door.platform}`}>
					{#if door.deep_link_available}
						{@const outcome = mintOutcomes[door.platform]}
						{@const paired = pairedOutcomes[door.platform]}
						{@const ttl = mintTtlSeconds[door.platform]}
						{@const chatLink = outcome ? conversationLink(outcome.deep_link) : null}
						{@const cd = outcome && ttl ? countdown(outcome.expires_at, nowMs, ttl) : null}
						<!-- Lit: a round, glowing, *alive* marker — the visual
						     opposite of the dark door's flat square below, by
						     design, not by opacity. -->
						<div class="flex items-center gap-2">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full"
								style={statusDotStyle('ample', STATUS_GOOD)}
								aria-hidden="true"
							></span>
							<p class="font-mono text-sm font-semibold text-amber-100">
								{doorLabel(door.platform)}
							</p>
						</div>

						{#if paired || door.paired}
							<div
								class="mt-3 border border-amber-700/60 bg-amber-950/30 p-4"
								data-testid={`paired-${door.platform}`}
								aria-live="polite"
							>
								<div class="flex items-center gap-2">
									<span class="text-lg text-amber-300" aria-hidden="true">✓</span>
									<p class="font-mono text-base font-semibold text-amber-100">connected</p>
								</div>
								<p class="mt-2 text-sm text-stone-300">
									{paired?.display ?? door.paired_display ?? 'Your chat'} is ready. Say hello to your
									resident.
								</p>
								{#if chatLink}
									<a
										class="mt-4 flex min-h-11 w-full items-center justify-center border border-emerald-500/70 bg-emerald-900/50 px-4 py-3 font-mono text-sm font-semibold tracking-wide text-emerald-50 uppercase hover:bg-emerald-800/60"
										href={chatLink}
										target="_blank"
										rel="external noreferrer"
										data-testid={`hello-${door.platform}`}>say hello</a
									>
								{/if}
							</div>
							<button
								type="button"
								class="mt-2 cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase underline hover:text-stone-300"
								onclick={() => mint(door.platform)}
								disabled={mintingPlatform === door.platform}
								>{mintingPlatform === door.platform ? 'minting…' : 'connect another chat'}</button
							>
						{:else if outcome && cd}
							<!-- The countdown: a designed element with three
							     distinct states, not a number in parens. -->
							<div class="mt-3">
								{#if cd.tier === 'critical'}
									<div class="flex items-center gap-2">
										<span
											class="inline-block h-2 w-2 shrink-0 rounded-full"
											style={statusDotStyle('critical', STATUS_SPENT)}
											aria-hidden="true"
										></span>
										<p
											class="font-mono text-[11px] tracking-wide uppercase"
											style={`color: ${STATUS_SPENT}`}
										>
											expired
										</p>
									</div>
									<button
										type="button"
										data-testid={`remint-${door.platform}`}
										class="mt-2 inline-flex cursor-pointer items-center border border-sky-700/70 bg-sky-950/30 px-3 py-2 font-mono text-[11px] tracking-wide text-sky-200 uppercase hover:bg-sky-900/40 hover:text-sky-100 disabled:cursor-not-allowed disabled:opacity-60"
										onclick={() => mint(door.platform)}
										disabled={mintingPlatform === door.platform}
										>{mintingPlatform === door.platform
											? 'opening…'
											: `re-mint ${doorLabel(door.platform).toLowerCase()} link`}</button
									>
								{:else}
									<div class="flex items-center gap-2">
										<div
											class="h-1 w-16 overflow-hidden rounded-full bg-stone-900"
											aria-hidden="true"
										>
											<div
												class="h-full transition-[width] duration-1000 ease-linear"
												style={`width: ${Math.max(0, Math.min(100, (cd.secondsLeft / ttl) * 100))}%; background-color: ${tierColor(cd.tier)};`}
											></div>
										</div>
										<span
											class="font-mono text-[11px] tabular-nums"
											style={`color: ${tierColor(cd.tier)}`}
											data-testid={`countdown-${door.platform}`}>{cd.label}</span
										>
										<span class="font-mono text-[10px] text-ink-mute uppercase"
											>{cd.tier === 'low' ? 'expiring soon' : 'expires in'}</span
										>
									</div>
									<div class="mt-2 flex flex-wrap items-center gap-3">
										{#if outcome.deep_link}
											<a
												class="inline-flex cursor-pointer items-center border border-sky-700/70 bg-sky-950/30 px-3 py-2 font-mono text-[11px] tracking-wide text-sky-200 uppercase hover:bg-sky-900/40 hover:text-sky-100"
												href={outcome.deep_link}
												target="_blank"
												rel="external noreferrer"
												data-testid={`open-${door.platform}`}
												>open {doorLabel(door.platform).toLowerCase()}</a
											>
										{:else}
											<div class="font-mono text-[11px] text-amber-100">{outcome.pair_code}</div>
										{/if}
										<!-- First-class, not an error path: reminting lives
										     beside the live link the whole time it's alive,
										     not only once it dies. -->
										<button
											type="button"
											data-testid={`remint-${door.platform}`}
											class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase underline hover:text-stone-300 disabled:cursor-not-allowed disabled:opacity-60"
											onclick={() => mint(door.platform)}
											disabled={mintingPlatform === door.platform}
											>{mintingPlatform === door.platform ? 'minting…' : 're-mint'}</button
										>
									</div>
									{#if !outcome.deep_link}
										<p class="mt-1.5 text-sm text-stone-400">{outcome.instructions}</p>
									{/if}
								{/if}
							</div>
						{:else}
							<p class="mt-1.5 text-sm text-stone-300">
								Tap to mint a link that binds this account — Telegram/WhatsApp open the app
								directly.
							</p>
							<button
								type="button"
								data-testid={`connect-${door.platform}`}
								class="mt-2 inline-flex cursor-pointer items-center border border-sky-700/70 bg-sky-950/30 px-3 py-2 font-mono text-[11px] tracking-wide text-sky-200 uppercase hover:bg-sky-900/40 hover:text-sky-100 disabled:cursor-not-allowed disabled:opacity-60"
								onclick={() => mint(door.platform)}
								disabled={mintingPlatform === door.platform}
								>{mintingPlatform === door.platform
									? 'opening…'
									: `connect ${doorLabel(door.platform).toLowerCase()}`}</button
							>
							{#if mintFailedPlatforms[door.platform]}
								<p class="mt-2 text-sm text-stone-400" data-testid={`mint-failed-${door.platform}`}>
									Couldn't reach brnrd — try again.
								</p>
							{/if}
						{/if}
					{:else}
						<!-- Dark: a flat, unglowed square — deliberately off,
						     not a dimmed copy of the lit door above. No CTA at
						     all when there's no lever to pull (`not_built`);
						     a quiet docs pointer when there is
						     (`not_configured`). -->
						<div class="flex items-center gap-2">
							<span class="inline-block h-2 w-2 shrink-0 border border-stone-600" aria-hidden="true"
							></span>
							<p class="font-mono text-sm font-semibold text-ink-quiet">
								{doorLabel(door.platform)}
							</p>
							<span
								class="ml-auto shrink-0 border border-stone-800 px-1.5 py-0.5 font-mono text-[9px] tracking-wide text-ink-mute uppercase"
								>off</span
							>
						</div>
						<p class="mt-1.5 text-sm text-ink-mute">{doorOffCopy(door.reason)}</p>
						{#if doorOffHasEnablePath(door.reason)}
							<a
								class="mt-1.5 inline-block font-mono text-[11px] tracking-wide text-sky-400 underline hover:text-sky-300"
								href={DOCS_URL}
								rel="external">how to configure this door — docs</a
							>
						{/if}
					{/if}
				</div>
			{/each}
		</div>
	</section>
{/if}
