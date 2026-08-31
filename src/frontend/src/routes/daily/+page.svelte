<script lang="ts">
	// /daily — the same dashboard `/` renders, with one section swapped.
	//
	// The first `/daily` was a bespoke composition: its own header, its own
	// buoy strip, islands, a kb reef, a cloth digest — a second, thinner
	// account of the same facts the main UI already states. Ditched
	// 2026-08-31 on the maintainer's own read ("there is nothing to salvage
	// there… have on the daily the whole main user interface, the visual,
	// like exactly as it is"). What survived is the part he kept: the
	// compacted live bars, and the ascii field as the scene.
	//
	// So the difference between this route and `/` is exactly one prop. On
	// `/`, the live-runs view is `ResidentField` and pressing a cell opens
	// that run's node panel. Here it is the room's map plus those compacted
	// bars, and pressing one opens the map full-screen with a collapse back
	// ("the live runs view would be that map and the compacted run bars that
	// whenever you press on it or expand, it actually opens the full screen
	// map. And then it should have a button to collapse and go back.").
	import Dashboard from '$lib/Dashboard.svelte';
</script>

<svelte:head><title>daily · brnrd</title></svelte:head>

<Dashboard liveView="map" />
