// The old app lives under /dashboard (web-main). spa.py sends a 308 for the moved heads in production; this reroute
// renders the same page on the old path wherever no server stands in front (vite dev, the repro drivers), so a
// bookmark or a driver on /runs/... still lands. The heads are read off routes/dashboard/*, not listed twice.
import type { Reroute } from '@sveltejs/kit';

const HEADS = new Set(
	Object.keys(import.meta.glob('./routes/dashboard/*/**/+page.svelte')).map((p) => p.split('/')[3])
);

export const reroute: Reroute = ({ url }) => {
	const head = url.pathname.split('/')[1];
	if (HEADS.has(head)) return '/dashboard' + url.pathname;
};
