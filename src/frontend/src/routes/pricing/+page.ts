// This route never touches authState / fetchLoginContext / /v1/dashboard/*
// (verified by grep before this override was added) — nothing here depends
// on the client-fetched auth probe the root layout's `ssr = false` exists to
// protect (see +layout.ts and Dashboard.svelte:171-176). Prerendering it
// bakes a real <title>/description/og/twitter into the raw HTML for
// crawlers that don't run JavaScript.
export const prerender = true;
export const ssr = true;
