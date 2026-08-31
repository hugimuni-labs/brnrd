<script lang="ts">
	import { runFace, type RunFace } from './runFace';

	/**
	 * One visual vocabulary for a run's topics.
	 *
	 * A topic owns one rune+hue; a run simply wears every topic it touched.
	 * Keep this renderer shared between the cloth, the live pick and the run
	 * node so those surfaces cannot drift back into "crossing strip + rune"
	 * or "first topic only" variants of the same fact.
	 */
	interface Props {
		topicIds?: readonly string[] | null;
		topicFaces?: ReadonlyMap<string, RunFace>;
		className?: string;
		label?: string;
	}

	let {
		topicIds = [],
		topicFaces = new Map<string, RunFace>(),
		className = '',
		label = 'topics touched'
	}: Props = $props();

	// Joins can legitimately mention the same topic more than once. A run wears
	// a topic once, in source order; repetition here would imply magnitude.
	let ids = $derived([...new Set(topicIds ?? [])]);
	let accessibleLabel = $derived(`${label}: ${ids.join(', ')}`);
</script>

{#if ids.length > 0}
	<span
		class={`inline-flex shrink-0 items-baseline gap-px font-mono ${className}`}
		title={accessibleLabel}
		aria-label={accessibleLabel}
	>
		{#each ids as topicId (topicId)}
			{@const face = topicFaces.get(topicId) ?? runFace(topicId)}
			<span aria-hidden="true" style={`color: ${face.color}`} title={topicId}>{face.glyph}</span>
		{/each}
	</span>
{/if}
