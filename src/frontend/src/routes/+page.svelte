<script lang="ts">
	// The resident dashboard — the main UI, in one place. The page body moved
	// to `$lib/Dashboard.svelte` on 2026-08-31 so `/daily` could wear the same
	// interface instead of growing a second, half-shaped one beside it
	// (maintainer: "have on the daily the whole main user interface, the
	// visual, like exactly as it is"). This route is the URL and nothing else;
	// its render is the component's default (`liveView="field"`), which is the
	// exact shape this file held before the extraction.
	import Dashboard from '$lib/Dashboard.svelte';
</script>

<Dashboard />
