// See routes/pricing/+page.ts for the rationale: this route never touches
// authState, so it is safe to override the root layout's `ssr = false` and
// bake its head into a prerendered file.
export const prerender = true;
export const ssr = true;
