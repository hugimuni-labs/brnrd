<script lang="ts">
	// The expanded scene: the same `AsciiField` the compact live-runs view
	// shows, standing at full height inside `RunOverlay`'s stage. It exists as
	// its own file only so the row arithmetic and the viewport binding sit
	// next to each other rather than inside the dashboard's already-long
	// script — it holds no state the reader can see and no semantics at all.
	import AsciiField from '$lib/AsciiField.svelte';
	import { mapRows } from '$lib/daily/daily';

	let viewportHeight = $state(0);
	let rows = $derived(mapRows('full', viewportHeight));
</script>

<svelte:window bind:innerHeight={viewportHeight} />

<div class="stage">
	<AsciiField {rows} header={false} legendDefault={false} />
</div>

<style>
	.stage {
		overflow: hidden;
		border: 1px solid rgba(217, 164, 65, 0.3);
		background: #0c0906;
	}
</style>
