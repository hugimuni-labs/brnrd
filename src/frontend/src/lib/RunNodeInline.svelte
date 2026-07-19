<script lang="ts">
	// The run node as the selected loom frame's content, rather than a page you
	// leave the loom to reach (maintainer, 2026-07-19: "let's keep the loom as
	// the spine"). Same composition as `RunNode.svelte`, same corpus response
	// already on this page — but a *targeted* read: the run's `## Now`, its
	// vitals, and a count. Everything heavier sits behind one expand, and the
	// standalone `/runs/...` page stays the addressable deep link.
	import MarkdownContent from './MarkdownContent.svelte';
	import type { HeartbeatLevel } from './liveRuns';
	import {
		messageInstant,
		messageTarget,
		messageTone,
		nodeDigest,
		runNodeFromSurface
	} from './runNode';
	import { STATUS_GOOD, STATUS_WARN, STATUS_UNKNOWN, statusDotStyle } from './statusPalette';
	import type { SurfaceResponse } from './surface';
	import { glitchReveal, typeReveal } from './transitions';

	interface Props {
		data: SurfaceResponse | null;
		repoSlug: string;
		runId: string;
		href: string;
		/**
		 * What the live packet and the ledger receipt know that the node's own
		 * files don't — elapsed, runner, phase, produce counts. This panel is
		 * the *only* rendering of a selected run now (2026-07-19: "the live run
		 * kinda duplicates the info"), so those facts fold in here rather than
		 * arriving as a second card above saying the same thing differently.
		 */
		vitals?: string[];
		/**
		 * Heartbeat freshness when the run is live (`liveRuns.heartbeatLevel`),
		 * `null` for a closed run. The panel absorbed the LiveRuns card as the
		 * one rendering of a selected run, but it dropped the card's liveness
		 * language on the floor — the status dot and the scanning bar are how
		 * this dashboard says "in motion", and a live node without them read as
		 * off-theme next to every other live surface (maintainer, 2026-07-19).
		 */
		liveLevel?: HeartbeatLevel | null;
	}

	let { data, repoSlug, runId, href, vitals = [], liveLevel = null }: Props = $props();

	const LIVE_COLOR: Record<HeartbeatLevel, string> = {
		running: STATUS_GOOD,
		stalling: STATUS_WARN,
		unknown: STATUS_UNKNOWN
	};

	let expanded = $state(false);
	let node = $derived(data ? runNodeFromSurface(data, repoSlug, runId) : null);
	let digest = $derived(node ? nodeDigest(node) : null);
	let knownPaths = $derived(new Set((data?.files ?? []).map((file) => file.path)));

	// Composed here rather than inline: `typeReveal` needs the exact string it
	// is revealing, and a `{@const}` cannot be declared inside a plain element.
	let heading = $derived(`run node${digest?.stage ? ` · ${digest.stage}` : ''}`);
	let identity = $derived(
		`${digest?.status || 'unknown'}${digest?.runner ? ` · ${digest.runner}` : ''}`
	);
	/** The empty-state lines are text that *arrives* too — a run that has just
	 *  started genuinely transitions through "no card written yet". */
	let cardEmptyLabel = $derived(
		digest?.status === 'running' ? 'no card written yet' : 'this run wrote no card'
	);
	let produceEmptyLabel = $derived(
		digest?.status === 'running' ? 'nothing committed yet' : 'this run produced nothing'
	);

	const TONE_CLASS: Record<string, string> = {
		delivered: 'text-emerald-400/80',
		collected: 'text-emerald-400/60',
		pending: 'text-amber-400',
		undeliverable: 'text-red-400',
		unknown: 'text-ink-quiet'
	};

	function instantLabel(raw: string): string {
		if (!raw) return '';
		const timestamp = Date.parse(raw);
		return Number.isNaN(timestamp) ? raw : new Date(timestamp).toLocaleTimeString();
	}
</script>

{#if data === null}
	<p class="panel p-3 font-mono text-[11px] text-ink-quiet">reading the corpus…</p>
{:else if !digest?.mirrored}
	<!-- Not every selected run has a node: the corpus republishes on change, and
	     runs that closed before the weld never had one. Say so quietly here —
	     this is a supporting panel, not the page that owes a full explanation. -->
	<p class="panel p-3 font-mono text-[11px] text-ink-quiet">
		no run node mirrored for this run yet
	</p>
{:else}
	<!-- The panel assembles rather than fading in: same `glitchReveal` grammar
	     the loom's own frames use, so selecting a frame reads as this dashboard
	     rather than as a generic disclosure. Kept short — the reveal is a
	     flourish, not a wait. -->
	<div class="panel p-3" in:glitchReveal={{ duration: 260 }}>
		<div
			class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-stone-800 pb-2 font-mono text-[10px]"
		>
			<span class="flex min-w-0 items-center gap-1.5">
				{#if liveLevel}
					<span
						class="inline-block h-2 w-2 shrink-0 rounded-full"
						style={statusDotStyle(
							liveLevel === 'stalling' ? 'cooling' : 'burning',
							LIVE_COLOR[liveLevel]
						)}
						aria-hidden="true"
					></span>
				{/if}
				<span
					class="min-w-0 truncate tracking-wide text-amber-200 uppercase"
					use:typeReveal={{ text: heading }}
				>
					{heading}
				</span>
			</span>
			<span class="shrink-0 text-ink-quiet" use:typeReveal={{ text: identity }}>
				{identity}
			</span>
		</div>

		{#if vitals.length > 0}
			<!-- The vitals line: one row of live/receipt facts, in the node's own
			     header, instead of a whole second panel restating the run. -->
			<div
				class="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 font-mono text-[10px] text-ink-quiet"
			>
				{#each vitals as vital (vital)}
					<span use:typeReveal={{ text: vital }}>{vital}</span>
				{/each}
			</div>
		{/if}

		{#if digest.now}
			<div class="text-sm text-stone-300">
				<MarkdownContent
					markdown={digest.now}
					sourcePath={node?.body?.path ?? ''}
					{knownPaths}
					reveal
				/>
			</div>
		{:else}
			<p class="mt-2 font-mono text-[11px] text-ink-quiet" use:typeReveal={{ text: cardEmptyLabel }}>
				{cardEmptyLabel}
			</p>
		{/if}

		<!-- Produce, unexpanded. #486 collapsed this panel to be the only
		     rendering of a selected run and folded relic *counts* into the
		     vitals line — but a count is not a link, so the manifest
		     retreated to the shelf row's expansion. The run's own document
		     carries it now, so it sits where the run's other truth sits, and
		     it accrues while the run is still working rather than appearing
		     at stop. Deliberately above the expand: produce is the summary,
		     not the detail.

		     The heading renders unconditionally (maintainer, 2026-07-19: "the
		     current run view doesn't show any produce"). It used to be gated on
		     the section existing, which made "this run has made nothing yet"
		     and "this feature isn't deployed" the same blank space — and both
		     were true at once that morning, which is exactly why it was
		     unreadable. Absence is tensed the way #480 tensed the body: produce
		     accrues from commits, so a running run that hasn't committed has an
		     honestly empty manifest, and a closed one never made anything. -->
		<div class="mt-2 border-t border-stone-800/70 pt-2">
			<p class="font-mono text-[10px] tracking-wide text-ink-mute uppercase">produce</p>
			{#if digest.produce}
				<div class="mt-1 text-sm text-stone-300">
					<MarkdownContent
						markdown={digest.produce}
						sourcePath={node?.state?.path ?? ''}
						{knownPaths}
						reveal
					/>
				</div>
			{:else}
				<p
					class="mt-1 font-mono text-[11px] text-ink-quiet"
					use:typeReveal={{ text: produceEmptyLabel }}
				>
					{produceEmptyLabel}
				</p>
			{/if}
		</div>

		<div class="mt-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
			<div class="flex items-baseline gap-3 font-mono text-[10px]">
				{#if digest.hasMore}
					<button
						type="button"
						class="cursor-pointer tracking-wide text-ink-quiet uppercase hover:text-stone-300"
						onclick={() => (expanded = !expanded)}
					>
						{expanded ? '▾ less' : '▸ more'}
					</button>
				{/if}
				<span class="text-ink-mute">
					{digest.messageCount} message{digest.messageCount === 1 ? '' : 's'}
				</span>
			</div>
			<a
				{href}
				class="shrink-0 font-mono text-[10px] tracking-wide text-amber-300 uppercase hover:text-amber-100"
			>
				full node →
			</a>
		</div>

		{#if liveLevel}
			<!-- The "in motion" tell, same grammar as the LiveRuns grid: an
			     indeterminate scan while the heartbeat is fresh, a flatlined
			     full-width low-opacity track when it isn't. -->
			<div class="mt-2 h-1 overflow-hidden bg-stone-900" aria-hidden="true">
				<div
					class={`h-full ${liveLevel === 'running' ? 'w-1/3 animate-[loom-scan_1.4s_ease-in-out_infinite]' : 'w-full'}`}
					style={`background-color: ${LIVE_COLOR[liveLevel]}; opacity: ${liveLevel === 'running' ? 1 : 0.3}`}
				></div>
			</div>
		{/if}

		{#if expanded}
			<!-- The expand is where following costs something: the rest of the
			     body, and the run's own traffic with its receipts. Kept inside
			     the frame so the band never scrolls out from under the reader. -->
			<div
				class="mt-3 space-y-3 border-t border-stone-800 pt-3"
				in:glitchReveal={{ duration: 240 }}
			>
				{#if node?.body && digest.now !== node.body.markdown.trim()}
					<div class="text-sm text-stone-300">
						<MarkdownContent
							markdown={node.body.markdown}
							sourcePath={node.body.path}
							{knownPaths}
						/>
					</div>
				{/if}
				{#each node?.messages ?? [] as message (message.file.path)}
					{@const tone = messageTone(message.metadata.status)}
					{@const target = messageTarget(message.metadata)}
					<article class="border-l border-stone-800 pl-3">
						<div
							class="flex flex-wrap items-baseline justify-between gap-x-3 font-mono text-[10px]"
						>
							<span class="min-w-0 truncate text-amber-200/80">
								{message.metadata.kind || 'message'}{target ? ` → ${target}` : ''}
							</span>
							<span class="shrink-0 {TONE_CLASS[tone]}">
								{message.metadata.status || 'recorded'}
								{instantLabel(messageInstant(message.metadata))}
							</span>
						</div>
						<div class="text-sm text-stone-400">
							<MarkdownContent
								markdown={message.body}
								sourcePath={message.file.path}
								{knownPaths}
							/>
						</div>
					</article>
				{/each}
			</div>
		{/if}
	</div>
{/if}
