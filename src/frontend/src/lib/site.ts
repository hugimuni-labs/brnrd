import { writable } from 'svelte/store';

export type Site = {
	deployment: 'cloud' | 'loopback';
	tree: string | null;
	features: string[];
};

// undefined: not answered yet; null: older backend or development server.
export const site = writable<Site | null | undefined>(undefined);
let pending: Promise<Site | null> | undefined;
export function loadSite(): Promise<Site | null> {
	return (pending ??= fetch('/v1/site', { cache: 'no-store' })
		.then(async (r) => {
			if (!r.ok) return null;
			const s = await r.json();
			return s &&
				['cloud', 'loopback'].includes(s.deployment) &&
				(s.tree === null || typeof s.tree === 'string') &&
				Array.isArray(s.features)
				? (s as Site)
				: null;
		})
		.catch(() => null)
		.then((s) => {
			site.set(s);
			return s;
		}));
}

export function cloudOnly(path: string): boolean {
	return ['/dashboard', '/settings', '/login', '/connect', '/new', '/config-approve'].some(
		(route) => path === route || path.startsWith(route + '/')
	);
}
