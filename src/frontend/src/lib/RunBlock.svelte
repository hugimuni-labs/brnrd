<script lang="ts">
	import { runFace } from './runFace';
	import { machineBodyOnScreen, machineHeadFields } from './machineDock';
	import type { PickRow } from './pickLane';
	import { pitchAccent } from './statusPalette';

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
	 *
	 * `panel--pressable` / `panel--collapsed` (`layout.css`, 2026-08-03, the
	 * rack answers everywhere) are the shared collapse chrome this line has
	 * in common with the rail: a hover/focus perimeter highlight (stronger
	 * while `aria-expanded="true"`, since that tap folds rather than opens),
	 * and a desaturated corner-color once `docked` — the scrolled-away
	 * pointer form, not the ordinary parked-at-rest look, which stays
	 * exactly as it already reads.
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
	// THE FACE IN THREE TENSES piece 1: the lead's *mood* — how it feels,
	// distinct from `face` above (*who* it is). `lead.mood` rides the same
	// `PickRow.mood` field `pickLane.ts` already resolves through the wire's
	// `moodFace()` for every picking row; whatever run the head is showing
	// (his "when it is expanded..." rule above, and THE MACHINE BORROWS THE
	// SELECTION's own concurrent change to what `lead`/lookalikes resolve
	// to) is the run this reads from — this file never re-derives identity,
	// only asks the row it's already given for its mood.
	let mood = $derived(lead?.mood ?? null);
	// The rest frame (held) and one representative expression frame (the
	// "blink") — the two states `.dock-mood-rest`/`.dock-mood-blink`
	// (`layout.css`) alternate between. No sequence, or a one-frame one, has
	// nothing to alternate *to*: `blinkFrame` stays null and the markup below
	// renders the rest glyph alone, unanimated (same outcome reduced-motion
	// produces, arrived at honestly rather than faked).
	let restFrame = $derived(mood?.rest ?? mood?.sequences?.[0]?.[0] ?? mood?.glyph ?? null);
	let blinkFrame = $derived.by(() => {
		const seq = mood?.sequences?.[0];
		if (!seq || seq.length < 2) return null;
		const mid = seq[Math.floor(seq.length / 2)] ?? null;
		return mid && mid !== restFrame ? mid : null;
	});
	// `mood_pitch` is the gut→crown body axis, a colour, never a tempo
	// (`statusPalette.pitchAccent` — every other mood consumer reads it the
	// same way); the alternation's cadence lives in `layout.css` as one fixed
	// slow beat instead.
	let moodAccent = $derived(pitchAccent(mood?.pitch ?? null));
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
	class="panel panel--pressable relative w-full overflow-hidden px-3 py-1.5 text-left font-mono {docked
		? 'panel--collapsed'
		: ''}"
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
				{#if restFrame}
					<!-- THE FACE IN THREE TENSES piece 1: the lead's mood, worn beside
					     its name. Two glyphs share one grid cell so the swap between
					     them is a hard cut (`layout.css`'s `steps()` keyframes), never a
					     cross-fade; with no distinct blink frame this is just the rest
					     glyph, still. -->
					<span
						class="relative inline-grid shrink-0 place-items-center font-mono whitespace-pre"
						style={moodAccent ? `color: ${moodAccent}` : undefined}
						title={mood ? `mood: ${mood.name}` : undefined}
						aria-hidden="true"
					>
						<span class="[grid-area:1/1] {blinkFrame ? 'dock-mood-rest' : ''}">{restFrame}</span>
						{#if blinkFrame}
							<span class="[grid-area:1/1] dock-mood-blink">{blinkFrame}</span>
						{/if}
					</span>
				{/if}
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
