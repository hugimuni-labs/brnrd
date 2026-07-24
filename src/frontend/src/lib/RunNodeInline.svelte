<script lang="ts">
	// The run node as the selected loom frame's content, rather than a page you
	// leave the loom to reach (maintainer, 2026-07-19: "let's keep the loom as
	// the spine"). Same composition as `RunNode.svelte`, same corpus response
	// already on this page — but a *targeted* read: the run's `## Now`, its
	// vitals, and a count. Everything heavier sits behind one expand, and the
	// standalone `/runs/...` page stays the addressable deep link.
	import type { ResolvedPathname } from '$app/types';
	import MarkdownContent from './MarkdownContent.svelte';
	import MoodChip from './MoodChip.svelte';
	import { LiveRunsAuthError, moodFace, requestRunStop, type HeartbeatLevel } from './liveRuns';
	import {
		messageInstant,
		messageTarget,
		messageTone,
		nodeDigest,
		runNodeFromSurface,
		type NodeIdentity
	} from './runNode';
	import { STATUS_GOOD, STATUS_WARN, STATUS_UNKNOWN, statusDotStyle } from './statusPalette';
	import type { SurfaceResponse } from './surface';
	import { glitchReveal, typeReveal } from './transitions';

	interface Props {
		data: SurfaceResponse | null;
		repoSlug: string;
		runId: string;
		href: ResolvedPathname;
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
		/**
		 * Seam for tests, and the reason the stop lives here rather than on the
		 * loom (maintainer, 2026-07-19: "move the delete button, it should be on
		 * expanded view, similar to how we handle the repo disconnect on the
		 * repos page… main screen, but not on the loom… or at least not like
		 * that, eating space").
		 *
		 * #492 put the stop in the loom cell, where a `w-7` sibling button stole
		 * width from a 9px-text cell that had none to give — a destructive
		 * control competing for space with the run's own name. The loom is a
		 * *band*: its job is position and density, and it cannot afford an
		 * affordance that must be readable to be safe.
		 */
		stopRun?: (runId: string) => Promise<unknown>;
		/**
		 * The run's card-grammar identity (name, spawn chip, repo · kind,
		 * runner, age, status word) — the LiveRuns card's visual language,
		 * worn by this panel so the two renderings of a run share one header
		 * (2026-07-21: the node panel had dropped the run's *name* entirely,
		 * and flattened runner/phase/elapsed into one 10px vitals string).
		 */
		identity?: NodeIdentity | null;
	}

	let {
		data,
		repoSlug,
		runId,
		href,
		vitals = [],
		liveLevel = null,
		stopRun = requestRunStop,
		identity = null
	}: Props = $props();

	// Confirm-then-commit, in the repos page's own grammar: an explicit pair of
	// buttons rather than the loom's timed arm. #492's `loomStopGesture` armed on
	// one tap and lapsed after four seconds, which was the right answer for a
	// 20px target in a dense band — and that constraint is exactly what moving
	// the control removed. A visible `cancel` beside a visible `confirm stop`
	// says what a self-disarming glyph could only imply.
	let confirmingStop = $state(false);
	let stopPending = $state(false);
	let stopped = $state(false);
	let stopNote = $state<string | null>(null);

	async function commitStop() {
		confirmingStop = false;
		stopPending = true;
		try {
			await stopRun(runId);
			stopped = true;
			// Deliberately not "stopped": the daemon has not consumed it yet.
			stopNote = 'stopping — ends on the next daemon sync, partial work kept';
		} catch (e) {
			// A swallowed stop must be loud (the 2026-07-11 lesson): the reader
			// just tried to kill a burning run and nothing visible happened.
			stopNote =
				e instanceof LiveRunsAuthError
					? 'session expired — sign in again, then retry'
					: e instanceof Error
						? e.message
						: 'stop request failed';
		} finally {
			stopPending = false;
		}
	}

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
	// The header speaks the LiveRuns card's grammar: colored status word left,
	// age + panel marker right, then name/context/runner as their own lines —
	// each fact falling back to the node's own digest when the page's identity
	// source doesn't know it.
	let statusWord = $derived(identity?.status || digest?.status || 'unknown');
	// Same fallback chain as the status word: the page's identity source knows
	// the mood while the run is live (and carries the resolved glyph), the
	// node's own frame answers for a closed one (handle only). Both paths run
	// through `moodFace`, so an unknown handle degrades to the bare name and an
	// absent mood renders nothing at all.
	let mood = $derived(
		moodFace(
			identity?.mood || digest?.mood,
			identity?.moodGlyph,
			identity?.moodPitch,
			identity?.moodFrames,
			identity?.moodRest
		)
	);
	let cornerLabel = $derived([identity?.age, 'run node'].filter(Boolean).join(' · '));
	let runnerLine = $derived.by(() => {
		const runner = identity?.runner || digest?.runner || '';
		return runner ? `runner: ${runner}` : null;
	});
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
		<div class="border-b border-stone-800 pb-2">
			<div class="flex items-center justify-between gap-2 font-mono text-[10px]">
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
						class="min-w-0 truncate font-medium tracking-wide text-amber-200 uppercase"
						style={liveLevel ? `color: ${LIVE_COLOR[liveLevel]}` : undefined}
						use:typeReveal={{ text: statusWord }}
					>
						{statusWord}
					</span>
				</span>
				<span class="shrink-0 text-ink-quiet" use:typeReveal={{ text: cornerLabel }}>
					{cornerLabel}
				</span>
			</div>
			{#if identity?.name}
				<p class="mt-1.5 flex min-w-0 items-center gap-1.5">
					<span
						class="truncate text-sm font-medium text-amber-100"
						use:typeReveal={{ text: identity.name }}>{identity.name}</span
					>
					{#if identity.spawn}
						<span
							class="shrink-0 border border-amber-900/60 bg-amber-950/40 px-1 py-0.5 font-mono text-[9px] tracking-wide text-amber-300 uppercase"
							>↳ spawn</span
						>
					{/if}
				</p>
			{/if}
			{#if identity?.context}
				<p class="truncate text-xs text-ink-quiet" use:typeReveal={{ text: identity.context }}>
					{identity.context}
				</p>
			{/if}
			{#if runnerLine}
				<p class="font-mono text-[10px] text-stone-400" use:typeReveal={{ text: runnerLine }}>
					{runnerLine}
				</p>
			{/if}
			{#if mood}
				<!-- The face gets its own stage, centered (evt-…-v7od): at 9px inline
				     it read as debug text riding the status word, not as a face. -->
				<div class="mt-2 flex justify-center">
					<MoodChip face={mood} seed={identity?.mood ?? ''} variant="stage" />
				</div>
			{/if}
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
			<p
				class="mt-2 font-mono text-[11px] text-ink-quiet"
				use:typeReveal={{ text: cardEmptyLabel }}
			>
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
				<!-- A live run always offers the expand even with nothing more to
				     read: the stop control lives down there, and gating the only
				     way to reach it on unrelated content would strand it. -->
				{#if digest.hasMore || liveLevel}
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
				{#if liveLevel}
					<!-- The stop, at the bottom of the expand: destructive, so it sits
					     past everything a reader came here to read, and only exists
					     while the run is actually live. A closed run has nothing to
					     stop, and rendering a dead control is how a surface teaches
					     people to ignore it. -->
					<div
						class="flex flex-wrap items-center gap-2 border-t border-stone-800/70 pt-3 font-mono text-[10px]"
					>
						{#if stopped}
							<span class="tracking-wide text-amber-500 uppercase">stopping</span>
						{:else if confirmingStop}
							<button
								type="button"
								class="cursor-pointer border border-red-900/60 bg-stone-950/70 px-2 py-1 tracking-wide text-red-300 uppercase hover:bg-red-950/40 disabled:cursor-wait disabled:opacity-50"
								disabled={stopPending}
								onclick={commitStop}>{stopPending ? 'stopping' : 'confirm stop'}</button
							>
							<button
								type="button"
								class="cursor-pointer border border-stone-800 px-2 py-1 tracking-wide text-ink-quiet uppercase hover:text-stone-300"
								disabled={stopPending}
								onclick={() => (confirmingStop = false)}>cancel</button
							>
							<span class="text-ink-mute">partial work is kept; the thought does not resume</span>
						{:else}
							<button
								type="button"
								class="cursor-pointer border border-stone-800 px-2 py-1 tracking-wide text-ink-quiet uppercase hover:text-red-300"
								onclick={() => (confirmingStop = true)}>stop run</button
							>
						{/if}
						{#if stopNote}
							<!-- Receipt line: a tap that gets swallowed must never be silent
							     (found live 2026-07-11 on the spool rack's own taps). -->
							<span class="text-amber-400/90">{stopNote}</span>
						{/if}
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
