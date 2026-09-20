import { normalizePathname } from './seo';

const GOATCOUNTER_ENDPOINT = 'https://gurio.goatcounter.com/count';
const GOATCOUNTER_SCRIPT = 'https://gc.zgo.at/count.js';

// Keep acquisition analytics on the public discovery surface only. In
// particular, do not load GoatCounter on login/callback routes, authenticated
// dashboard routes, or legal/acceptance pages. This also makes query strings
// impossible to leak: pageviews are sent with a normalized pathname we choose.
const ANALYTICS_EXACT_PATHS = new Set(['/', '/pricing', '/learn', '/log']);
const ANALYTICS_PREFIXES = ['/learn/', '/log/'];

type GoatCounter = {
	count: (data?: { path?: string; title?: string; referrer?: string }) => void;
};

declare global {
	interface Window {
		goatcounter?: GoatCounter;
	}
}

let loadPromise: Promise<void> | null = null;

export function isAnalyticsPath(pathname: string): boolean {
	const normalized = normalizePathname(pathname);
	return (
		ANALYTICS_EXACT_PATHS.has(normalized) ||
		ANALYTICS_PREFIXES.some((prefix) => normalized.startsWith(prefix))
	);
}

function loadGoatCounter(): Promise<void> {
	if (typeof document === 'undefined') return Promise.resolve();
	if (window.goatcounter?.count) return Promise.resolve();
	if (loadPromise) return loadPromise;

	loadPromise = new Promise((resolve, reject) => {
		const script = document.createElement('script');
		script.dataset.goatcounter = GOATCOUNTER_ENDPOINT;
		script.dataset.goatcounterSettings = JSON.stringify({
			no_onload: true,
			no_events: true
		});
		script.async = true;
		script.src = GOATCOUNTER_SCRIPT;
		script.addEventListener('load', () => resolve(), { once: true });
		script.addEventListener(
			'error',
			() => {
				loadPromise = null;
				reject(new Error('failed to load GoatCounter'));
			},
			{ once: true }
		);
		document.head.appendChild(script);
	});

	return loadPromise;
}

export async function countPublicPageview(
	pathname: string,
	options: { title?: string; referrer?: string } = {}
): Promise<void> {
	const path = normalizePathname(pathname);
	if (!isAnalyticsPath(path) || typeof window === 'undefined') return;

	try {
		await loadGoatCounter();
		window.goatcounter?.count({
			path,
			title: options.title,
			referrer: options.referrer
		});
	} catch {
		// Analytics must never be able to break navigation or render paths.
	}
}
