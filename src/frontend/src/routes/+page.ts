// `/` renders <Dashboard/>, which branches client-side on a fetched
// authState ('unknown' | 'authed' | 'anon') — Dashboard.svelte:171-176,
// "three states, not two": a signed-in reader must never glimpse the
// anonymous landing and vice versa. That invariant is about the *body*.
//
// <head> is not the body. Prerendering this route bakes the raw HTML a
// crawler sees (title/description/og/twitter/canonical — see this route's
// own <svelte:head>) without changing what happens after hydration: SSR
// here only ever renders Dashboard's initial 'unknown' state (the same
// neutral first paint every visitor already saw before any client fetch
// resolved), and the client-side auth branch takes over exactly as before.
// A signed-in reader gets the same public head as a stranger; that is not
// a leak, and it is what every SaaS does.
//
// NOTE — this alone does not fix `/` in production yet. `/`'s prerendered
// output and vite.config.ts's adapter-static `fallback` file are both named
// `index.html`; the fallback is written second and silently overwrites the
// real one (SvelteKit warns "Overwriting build/index.html with fallback
// page" at build time — reproduced, see /tmp/brnrd-unfurl.md). Shipping the
// fix needs the fallback renamed *and* src/brnrd/spa.py's hardcoded
// fallback filename updated to match, in the same release — not done here,
// since that second half is outside src/frontend/. This override stays in
// place so `/` is already correct the moment that backend change lands.
export const prerender = true;
export const ssr = true;
