<script lang="ts">
	import { runFace } from './runFace';
	import { machineBodyOnScreen, machineHeadFields } from './machineDock';
	import type { PickRow } from './pickLane';

	/**
	 * The machine, parked: one line (his 08-02 steer, verbatim intent — "a
	 * run-line block, height of one line, where the name goes in that block,
	 * but on the background the face appears in the middle and kinda shadows
	 * the name"). The burning run's rune sits large and dim behind its name —
	 * the face's debut surface — and tapping the line unfolds the full lane.
	 *
	 * Single-flight honesty: one line carries the main run; further burning
	 * strands are a `+N`, not extra lines — they have real rows in the lane
	 * one tap away, and the parked form is a pulse, not an inventory.
	 */
	interface Props {
		burning: PickRow[];
		armed: PickRow[];
		open: boolean;
		onToggle: () => void;
		/** True while this line is stuck to the top of the viewport and the lane
		 *  it belongs to is back at the block's home, screens above. Then the
		 *  line is a pointer, not a disclosure — it renders exactly as it does
		 *  parked, because from here that is what it is, and the page's tap
		 *  handler takes the reader to the block rather than folding a body
		 *  they cannot see (`machineDock.ts`, THE DOCK THAT TAPPED WRONG). */
		docked?: boolean;
		/** The feed's own health — the parked line must not read "parked"
		 *  over a dead feed as if that were a fact about the machine. */
		error?: string | null;
		stale?: boolean;
	}
	let {
		burning,
		armed,
		open,
		onToggle,
		docked = false,
		error = null,
		stale = false
	}: Props = $props();
	let lead = $derived(burning[0] ?? null);
	let face = $derived(lead ? runFace(lead.id) : null);
	let nextArmed = $derived(armed[0] ?? null);
	// One predicate, and it is never `open` alone: with the lane on screen the
	// head keeps identity and drops every measurement that lane draws in full
	// (his "when it is expanded, it shouldn't repeat the both collapsed and
	// semi-expanded shape"); docked, the lane is nowhere near it and the head is
	// the only line there is. Rule and reasoning: `machineDock.ts`.
	let bodyOnScreen = $derived(machineBodyOnScreen(open, docked));
	let head = $derived(machineHeadFields(bodyOnScreen));
</script>

<button
	type="button"
	class="panel relative w-full cursor-pointer overflow-hidden px-3 py-1.5 text-left font-mono"
	aria-expanded={bodyOnScreen}
	aria-label={docked ? 'go to the machine' : open ? 'fold the machine' : 'expand the machine'}
	onclick={onToggle}
>
	{#if face}
		<!-- The face, watermark-sized: identity the eye catches before any
		     text. Dim on purpose — it shadows the name, never fights it. -->
		<span
			aria-hidden="true"
			class="pointer-events-none absolute inset-0 flex items-center justify-center text-4xl leading-none opacity-20"
			style={`color: ${face.color}`}>{face.glyph}</span
		>
	{/if}
	<span class="relative flex w-full flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[10px]">
		<!-- `▸` is the rail's own docked marker, and it means the same thing in
		     both places: there is more, and it is one tap away. -->
		<span class="tracking-[0.13em] text-ink-quiet uppercase"
			>{bodyOnScreen ? '▾' : '▸'} machine</span
		>
		{#if lead}
			<span class="flex min-w-0 items-baseline gap-1.5 text-amber-200">
				{#if face}<span aria-hidden="true" style={`color: ${face.color}`}>{face.glyph}</span>{/if}
				<span class="max-w-[26ch] truncate text-[11px]">{lead.label}</span>
			</span>
			{#if head.clock && lead.clock}<span class="text-amber-500/80">{lead.clock}</span>{/if}
			{#if head.note && lead.note}<span class="text-ink-quiet">{lead.note}</span>{/if}
			{#if head.extra && burning.length > 1}<span class="text-amber-500/80"
					>+{burning.length - 1}</span
				>{/if}
		{:else}
			<span class="text-ink-quiet">parked</span>
		{/if}
		<span class="ml-auto {error ? 'text-red-400' : 'text-ink-quiet'}">
			{#if error}
				<!-- Health is never suppressed while open: a dead feed is a fact about
				     the block, not a measurement the rows below repeat, and hiding it
				     would leave the lane looking authoritative over nothing. -->
				<span class="max-w-[32ch] truncate">{error}</span>
			{:else if head.armedTail && nextArmed}
				{armed.length} armed{nextArmed.clock ? ` · next ${nextArmed.clock}` : ''}{stale
					? ' · stale'
					: ''}
			{:else if stale}
				stale report
			{:else if head.armedTail && !lead}
				nothing armed
			{/if}
		</span>
	</span>
</button>
