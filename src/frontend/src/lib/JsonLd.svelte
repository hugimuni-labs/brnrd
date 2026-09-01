<script lang="ts">
	// A literal closing-script-tag substring anywhere in this file's own
	// source closes this very `<script>` block early, per the HTML spec —
	// true of a raw .html file and, it turns out, of this .svelte file's
	// own compiler/linter too (confirmed the hard way: writing that
	// sequence out in a comment above this line broke the build). Every
	// mention of it below is spelled with an escaping backslash for that
	// reason, and the tag itself is built from two concatenated halves so
	// the live sequence never appears contiguously in source.
	let { data }: { data: unknown } = $props();

	// Every `<` escaped to its unicode form so `<\/script>` / `<!--` can't
	// appear in the *payload* either, whatever `data` holds — the standard
	// mitigation for inlining JSON inside a live <script> element.
	let payload = $derived(JSON.stringify(data).replace(/</g, '\\u003c'));
	let openTag = '<' + 'script type="application/ld+json">';
	let closeTag = '<' + '/script>';
	let html = $derived(openTag + payload + closeTag);
</script>

<!-- eslint-disable-next-line svelte/no-at-html-tags -- built from a literal wrapper + JSON.stringify(data), every `<` escaped; data is always this app's own content, never user input -->
{@html html}
