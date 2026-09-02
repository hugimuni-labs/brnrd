// See routes/pricing/+page.ts for the rationale: this route never touches
// authState, so it is safe to override the root layout's `ssr = false` and
// bake its head into a prerendered file. Without this override /log inherits
// the layout default and every <svelte:head> tag this route writes (title,
// description, og:image, the Blog JsonLd) is client-only — invisible to a
// crawler or unfurler that never runs JS, which defeats the whole point of
// a public, indexable build log (found reviewing #1758, before merge: the
// route had no +page.ts at all).
export const prerender = true;
export const ssr = true;
