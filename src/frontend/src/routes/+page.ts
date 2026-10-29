// `/` is the board, the dashboard or the landing, decided client-side
// (+page.svelte). Prerendering bakes the raw HTML a crawler sees: the head
// (title/description/og, this route's own <svelte:head>) and, below, the body.
//
// 2026-10-29 (web-main): the body is prerendered too. +page.svelte draws the
// anonymous landing until its probe of /v1/loom/index decides, so the raw HTML
// carries the h1 and the install line; a signed-in reader sees it for one probe
// before the board or the dashboard replaces it (accepted, inbox xaj18). The
// adapter-static fallback is 200.html (vite.config.ts), so it no longer
// overwrites this route's index.html.
export const prerender = true;
export const ssr = true;
