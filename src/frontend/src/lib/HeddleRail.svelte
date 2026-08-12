<script lang="ts">
	import { RUNE_SPACE, type TopicCounts, type TopicThread } from './warpGraph';
	import HeddleStrip from './HeddleStrip.svelte';

	// The heddles — the topic rail (2026-08-11 proposal round). Topics are
	// the Photoshop-layers axis: a small collapsible block, a flat list —
	// never a tree, never a storage root. Each topic is a lens the reader
	// toggles; the lit set filters the warp and the cloth below. The rune
	// and hue are hashed from the canonical topic id (`topicFace`), so the
	// mark is stable across renames of the *set* — this is where the runes
	// live now that runs stopped wearing them (the mark doctrine: a topic
	// passes the two-places test a run fails).

	interface Props {
		threads: TopicThread[];
		counts: Map<string, TopicCounts>;
		/** Canonical ids currently lit; null = all lit (the default). */
		selected: ReadonlySet<string> | null;
		/** Topics with an item held by a live run — wear the weaving bolt. */
		weaving?: ReadonlySet<string>;
		onToggle?: (canonicalId: string) => void;
		onAll?: () => void;
		/** Tests seed the rail open; the page leaves it collapsed. */
		initialOpen?: boolean;
	}

	let {
		threads,
		counts,
		selected,
		weaving = new Set<string>(),
		onToggle = undefined,
		onAll = undefined,
		initialOpen = false
	}: Props = $props();

	// svelte-ignore state_referenced_locally
	let open = $state(initialOpen);

	const FOLD_ID = 'heddle-rail-fold';

	function isLit(id: string): boolean {
		return selected === null || selected.has(id);
	}

	let untagged = $derived(counts.get('') ?? null);
</script>

{#if threads.length > 0}
	<div class="subpanel px-3 py-2 text-xs">
		<div class="flex w-full flex-wrap items-baseline gap-x-2 gap-y-0.5">
			<button
				type="button"
				class="flex cursor-pointer items-baseline gap-x-2 text-left"
				aria-expanded={open}
				aria-controls={FOLD_ID}
				onclick={() => (open = !open)}
			>
				<span class="font-mono text-[10px] text-ink-quiet" aria-hidden="true"
					>{open ? '▾' : '▸'}</span
				>
				<span class="font-mono text-[11px] tracking-wide text-amber-200 uppercase">heddles</span>
			</button>
			<!-- The collapsed strip — chip, runes, `all` reset — is the legend:
			     every topic's rune, lit or dim, each a working toggle. Shared
			     with the docked sticky copy (`HeddleStrip.svelte`, `+page.svelte`)
			     so the two never fork: one control, one `heddleSelection`. -->
			<HeddleStrip {threads} {selected} {weaving} {onToggle} {onAll} />
		</div>
		{#if open}
			<ul class="mt-2 space-y-1" id={FOLD_ID}>
				{#each threads as thread (thread.canonicalId)}
					{@const lit = isLit(thread.canonicalId)}
					{@const count = counts.get(thread.canonicalId)}
					<li class="flex items-baseline gap-x-2">
						<button
							type="button"
							class="flex min-w-0 flex-1 cursor-pointer items-baseline gap-x-2 text-left"
							aria-pressed={lit}
							onclick={() => onToggle?.(thread.canonicalId)}
						>
							<!-- The layer-eye (his read: lean into the Photoshop-layers
							     idiom the strip already half-speaks). A filled dot is a
							     visibility toggle in every layers panel; a hollow one
							     reads "off" independent of color, so a row still reads as
							     a filter row when two topics' hues are close. -->
							<span
								class="shrink-0 font-mono text-[13px] leading-none"
								style={lit ? `color: ${thread.face.color}` : ''}
								class:text-ink-mute={!lit}
								aria-hidden="true">{lit ? '●' : '○'}</span
							>
							<span
								class="shrink-0 font-mono text-[16px] leading-none"
								style={lit ? `color: ${thread.face.color}` : ''}
								class:text-ink-mute={!lit}
								class:opacity-50={!lit}
								aria-hidden="true">{thread.face.glyph}</span
							>
							<span
								class="min-w-0 flex-1 break-words"
								class:text-amber-100={lit}
								class:text-ink-quiet={!lit}>{thread.title}</span
							>
							{#if weaving.has(thread.canonicalId)}
								<span class="shrink-0 text-amber-300/90" aria-label="weaving now">↯</span>
							{/if}
							{#if count}
								<span class="shrink-0 font-mono text-[10px] text-ink-quiet">
									{count.ready} ready{count.blocked > 0 ? ` · ${count.blocked} held` : ''}
								</span>
							{/if}
						</button>
					</li>
				{/each}
				{#if threads.length > RUNE_SPACE}
					<!-- The topic cap is the rune space (his 2026-08-11 word): within
					     24 topics every topic wears a unique stave; past it the probe
					     runs out of alphabet and collisions are pigeonhole. Merging
					     topics is the fix; this line is the honest gauge. -->
					<li class="font-mono text-[10px] text-amber-300/80">
						{threads.length} topics — past the rune space ({RUNE_SPACE}); staves now collide. merge
						topics.
					</li>
				{/if}
				{#if untagged}
					<!-- Untagged open items are a fact, not a topic: named here so
					     a filtered view's "missing" items are accounted for. -->
					<li class="font-mono text-[10px] text-ink-mute">
						· untagged {untagged.ready + untagged.blocked} — pass only the all-lit view
					</li>
				{/if}
			</ul>
		{/if}
	</div>
{/if}
