<script lang="ts">
	// The buoy's own address, in place: pressing a /daily buoy used to leave
	// the page for /warp (evt-1788122610992158000-vm1f, 2026-08-30, "the
	// anchoring URLS should stay on the /daily"). This panel is the answer —
	// the one item WarpGraphView would have unfolded inline, staged instead
	// inside RunOverlay's own sheet, /daily's existing placement for "a run,
	// pressed, opens a panel." Deliberately thin: no heddle rail, no repo
	// lens, no live-taken framing — those are /warp's job on its own page
	// (`WarpGraphView.svelte`, not touched here) and this stub only needs
	// title/type/topics/state/needs/body per the ask.
	import MarkdownContent from '../MarkdownContent.svelte';
	import {
		STATUS_BURNING,
		STATUS_COOLING,
		STATUS_SPENT,
		STATUS_UNKNOWN,
		STATUS_WARN
	} from '../statusPalette';
	import {
		blockers,
		resolveTopics,
		topicFaces,
		type ItemType,
		type WarpGraph,
		type WarpItem
	} from '../warpGraph';
	import { dailyItemState, type DailyItemState } from './daily';

	interface Props {
		item: WarpItem;
		graph: WarpGraph;
	}

	let { item, graph }: Props = $props();

	// Same four-lane palette WarpGraphView draws its type dot from — kept as
	// a local copy rather than an import, so this page never has to reach
	// into /warp's own component to read it (that file stays untouched).
	const TYPE_COLOR: Record<ItemType, string> = {
		decision: STATUS_WARN,
		preparation: STATUS_COOLING,
		action: STATUS_BURNING,
		goal: STATUS_UNKNOWN
	};
	const TYPE_MARK: Record<ItemType, string> = {
		decision: '◆',
		preparation: '◇',
		action: '●',
		goal: '◎'
	};
	const STATE_LABEL: Record<DailyItemState, string> = {
		ready: 'ready',
		blocked: 'blocked',
		taken: 'taken',
		done: 'done',
		retired: 'retired'
	};
	const STATE_COLOR: Record<DailyItemState, string> = {
		ready: STATUS_BURNING,
		blocked: STATUS_SPENT,
		taken: STATUS_WARN,
		done: '#8a827a',
		retired: '#8a827a'
	};

	let topics = $derived(resolveTopics(item, graph));
	let faces = $derived(topicFaces(graph));
	let state = $derived(dailyItemState(item, graph));
	let needs = $derived(blockers(item, graph));
</script>

<div class="border border-stone-800 bg-stone-950/95 p-4 font-mono text-stone-300 sm:p-5">
	<div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
		<span
			class="shrink-0 text-sm"
			style={item.type ? `color: ${TYPE_COLOR[item.type]}` : `color: ${STATUS_UNKNOWN}`}
			aria-hidden="true">{item.type ? TYPE_MARK[item.type] : '▫'}</span
		>
		<h2 class="min-w-[9ch] flex-1 text-sm leading-snug font-semibold text-amber-100">
			{item.headline}
		</h2>
		<span class="shrink-0 text-[10px] text-ink-mute">{item.id}</span>
	</div>

	<div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] tracking-wide uppercase">
		<span style={`color: ${STATE_COLOR[state]}`}>{STATE_LABEL[state]}</span>
		{#if item.type}<span class="text-ink-quiet">{item.type}</span>{/if}
		{#if topics.length > 0}
			<span class="flex items-center gap-1 normal-case" aria-label="topics">
				{#each topics as topic (topic.canonicalId)}
					{@const face = faces.get(topic.canonicalId)}
					{#if face}
						<span style={`color: ${face.color}`} title={topic.title}>{face.glyph}</span>
					{/if}
				{/each}
			</span>
		{/if}
	</div>

	{#if needs.open.length > 0 || needs.dangling.length > 0}
		<p class="mt-2 text-[10px] text-ink-quiet">
			needs
			{#each needs.open as blocker (blocker.id)}
				<span class="ml-1 text-stone-300">{blocker.id} {blocker.headline}</span>
			{/each}
			{#each needs.dangling as id (id)}
				<span class="ml-1 text-red-400/80" title="needs an item that does not exist">{id}?</span>
			{/each}
		</p>
	{/if}

	{#if item.prompt}
		<!-- For many items — decisions especially — the `prompt:` row *is* the
		     content; a panel that renders only bodyMarkdown calls a stated
		     question "no body text" (measured on the real w-1, 2026-08-30). -->
		<p class="mt-3 border-l-2 border-amber-700/50 pl-2 text-[11px] leading-snug text-stone-300">
			{item.prompt}
		</p>
	{/if}

	{#if item.refs.length > 0}
		<p class="mt-2 text-[10px] text-ink-quiet">
			refs
			{#each item.refs as ref (ref.label)}
				{#if ref.href}
					<a class="ml-1 text-amber-200/80 underline decoration-amber-200/30" href={ref.href}
						>{ref.label}</a
					>
				{:else}
					<span class="ml-1 text-stone-400">{ref.label}</span>
				{/if}
			{/each}
		</p>
	{/if}

	{#if item.bodyMarkdown}
		<div class="mt-3 text-[11px]">
			<MarkdownContent markdown={item.bodyMarkdown} sourcePath={item.path} />
		</div>
	{:else if !item.prompt}
		<p class="mt-3 text-[11px] text-ink-quiet italic">no body text on this item.</p>
	{/if}
</div>
