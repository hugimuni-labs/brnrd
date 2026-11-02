// Every link out of the app opens in a new tab (w-lmxpqx5558uf), as one rule rather than a target per anchor: the
// root layout runs this on each click, in the capture phase, so the anchor carries its target before the browser
// (or SvelteKit's router, which leaves a targeted anchor alone) acts on it.
export function outbound(e: MouseEvent, here: string = location.origin): void {
	const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
	if (!a || a.hasAttribute('download') || !isOutbound(a.href, here)) return;
	a.target = '_blank';
	a.rel = 'noopener';
}

// Outbound: an http(s) address on another origin. mailto:, tel: and in-page or same-origin paths stay put.
export function isOutbound(href: string, here: string): boolean {
	try {
		const u = new URL(href, here);
		return /^https?:$/.test(u.protocol) && u.origin !== new URL(here).origin;
	} catch {
		return false;
	}
}
