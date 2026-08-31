<script lang="ts">
	// The live-runs view `/daily` wears, in the slot `ResidentField` holds on
	// `/` (maintainer, 2026-08-31: "the live runs view would be that map and
	// the compacted run bars that whenever you press on it or expand, it
	// actually opens the full screen map. And then it should have a button to
	// collapse and go back.").
	//
	// Two things and no third: the compacted presence bars — the one part of
	// the first `/daily` he kept, carried over with their own styling intact —
	// and the ascii field beneath them as the scene. Neither is a new
	// rendering of anything: the bars are `dailyLiveBars` unchanged, the scene
	// is the same `AsciiField` `/ascii` renders. This component owns no
	// semantics at all; it decides where the two sit and reports a press.
	//
	// Press is one verb here, deliberately. A bar and the expand control both
	// mean *show me the room, properly* — the compact view's whole job is to
	// cost nothing until asked, so it does not grow a second gesture with a
	// second meaning. (What it does not yet do: carry the pressed run into the
	// opened map as the camera's subject. `AsciiField` follows the lead actor
	// and takes no focus prop; giving it one is the map's own rung, not this
	// one.)
	import AsciiField from '$lib/AsciiField.svelte';
	import { dailyLiveBars, mapRows } from '$lib/daily/daily';
	import type { LiveRun } from '$lib/liveRuns';

	interface Props {
		runs: LiveRun[];
		stale?: boolean;
		/** A press on any bar, or on the expand control: open the full stage. */
		onExpand: () => void;
	}

	let { runs, stale = false, onExpand }: Props = $props();

	let bars = $derived(dailyLiveBars(runs));
	// The scene reads the viewport rather than standing at a constant row
	// count — see `mapRows`. `bind:innerHeight` is 0 on the server and until
	// the first client measurement, which `mapRows` floors rather than
	// rendering an empty frame.
	let viewportHeight = $state(0);
	let rows = $derived(mapRows('inline', viewportHeight));
</script>

<svelte:window bind:innerHeight={viewportHeight} />

<section class="live-map" aria-label="the room, live">
	<div class="section-heading">
		<h3>now · the room</h3>
		<div class="heading-right">
			<span>{runs.length} awake{stale ? ' · stale' : ''}</span>
			<button
				type="button"
				class="expand"
				onclick={onExpand}
				aria-label="expand the map full screen">⤢ expand</button
			>
		</div>
	</div>

	{#if bars.length === 0}
		<p class="quiet-row">the surface is still — nothing is burning right now.</p>
	{:else}
		<ul class="bars" aria-label="live runs">
			{#each bars as bar (bar.run.id)}
				<li style={`--nest: ${bar.depth}`}>
					<button class="live-bar" type="button" onclick={onExpand}>
						<span class="run-face"
							>{bar.run.mood_rest || bar.run.mood_glyph || (bar.depth ? 'a' : 'b·_·d')}</span
						>
						<span class="run-name">{bar.name}</span>
						{#if bar.act}<span class="run-act">{bar.act}</span>{/if}
						{#if bar.course}<span class="datum">C {bar.course}</span>{/if}
						{#if bar.pending > 0}<span class="datum">✉ {bar.pending}</span>{/if}
						<span class="open-mark">▸</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}

	<div class="field-frame">
		<AsciiField {rows} header={false} legendDefault={false} />
	</div>
</section>

<style>
	.section-heading {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.45rem;
		color: #d6b878;
		font-family: ui-monospace, monospace;
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.14em;
	}
	.section-heading h3 {
		font-size: 0.72rem;
		font-weight: 700;
	}
	.heading-right {
		display: flex;
		align-items: baseline;
		gap: 0.7rem;
	}
	.expand {
		cursor: pointer;
		border: 1px solid rgba(217, 164, 65, 0.35);
		padding: 0.1rem 0.4rem;
		color: #d6b878;
		letter-spacing: 0.1em;
	}
	.expand:hover {
		border-color: rgba(251, 191, 36, 0.7);
		color: #fbbf24;
	}
	.bars {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.live-bar {
		cursor: pointer;
		width: calc(100% - var(--nest) * 1.25rem);
		margin-left: calc(var(--nest) * 1.25rem);
		display: grid;
		grid-template-columns: auto minmax(7rem, auto) minmax(8rem, 1fr) auto auto auto;
		align-items: center;
		gap: 0.65rem;
		border: 1px solid rgba(217, 164, 65, 0.27);
		background: rgba(35, 25, 13, 0.72);
		padding: 0.48rem 0.65rem;
		text-align: left;
		font-family: ui-monospace, monospace;
		font-size: 0.68rem;
		color: #d6d3d1;
	}
	.live-bar:hover {
		border-color: rgba(251, 191, 36, 0.68);
		background: rgba(65, 42, 16, 0.72);
	}
	.run-face,
	.datum {
		color: #fbbf24;
		white-space: nowrap;
	}
	.run-name {
		color: #fef3c7;
		font-weight: 700;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.run-act {
		color: #a8a29e;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.open-mark {
		color: #8a827a;
	}
	.quiet-row {
		border: 1px dashed rgba(217, 164, 65, 0.22);
		padding: 0.7rem;
		font-family: ui-monospace, monospace;
		font-size: 0.7rem;
		color: #a8a29e;
	}
	.field-frame {
		margin-top: 0.5rem;
		overflow: hidden;
		border: 1px solid rgba(217, 164, 65, 0.3);
		background: #0c0906;
		box-shadow: 0 0 35px rgba(217, 164, 65, 0.05);
	}
	/* Phone: the bar's six columns don't fit, so the act line wraps under the
	   name and the open mark (which the whole bar already is) goes away —
	   carried verbatim from the first `/daily`, which is the part of it the
	   maintainer kept. */
	@media (max-width: 600px) {
		.live-bar {
			grid-template-columns: auto minmax(0, 1fr) auto auto;
			gap: 0.4rem;
		}
		.run-act {
			grid-column: 2/-1;
			font-size: 0.58rem;
		}
		.open-mark {
			display: none;
		}
	}
</style>
