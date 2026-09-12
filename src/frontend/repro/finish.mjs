// Teardown every repro driver needs and none of them had.
//
// The measurement (#1944, 2026-09-12): of the sixteen `ui-shots` runs that had
// ever existed, not one had succeeded. With the job bounded and the child's
// stderr inherited, each driver in turn showed the same thing on a CI runner —
// it printed its **entire** payload, did every bit of its work, and then never
// exited. `drive-fuel` was SIGKILLed at four minutes having finished in twenty
// seconds. Locally all three return cleanly, which is why three weeks of
// cancelled runs read as flakiness.
//
// A capture driver's contract is: write the files, print the line, return. An
// unbounded `browser.close()` is the one thing standing between doing the job
// and reporting it, and a handle nobody can name is not a reason to lose the
// work. Three copies of that reasoning would be three places to drift, so it
// lives here and each driver spends one line on it.

import { setTimeout as delay } from 'node:timers/promises';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { normalizeModule } from './shot-coverage.mjs';

/** Close a Playwright browser, bounded, narrated. Never throws. */
export async function closeBrowser(browser, label, ms = 15_000) {
	process.stderr.write(`[${label}] closing browser\n`);
	try {
		await Promise.race([browser.close(), delay(ms)]);
	} catch (err) {
		process.stderr.write(`[${label}] close failed: ${err?.message ?? err}\n`);
	}
	process.stderr.write(`[${label}] browser closed\n`);
}

/** Signal a spawned vite, narrated. Never throws. */
export function stopVite(vite, label) {
	try {
		vite.kill('SIGTERM');
	} catch {
		/* already gone */
	}
	process.stderr.write(`[${label}] vite signalled\n`);
}

/**
 * Run a driver's `main` and *return* — the part that was missing.
 *
 * stdout is flushed before the exit is forced: this process's stdout is the
 * JSON payload `ci-shots.mjs` embeds in its summary, and `process.exit` can
 * truncate a pipe.
 */
export function runDriver(main) {
	main()
		.then(async () => {
			await new Promise((resolve) => process.stdout.write('', resolve));
			process.exit(0);
		})
		.catch((err) => {
			console.error(err);
			process.exit(1);
		});
}

/**
 * Record which source modules the captured pages actually *ran* (#1944).
 *
 * The screenshot check used to be able to report "0 px changed" for a PR whose
 * only changed component no driver opens — a green tick meaning *nothing
 * looked*. The first fix recorded what the browser fetched, and measured
 * wrong in the flattering direction: the deck route imports 108 source modules
 * including the run-ledger receipt it never renders, so the very PR that
 * exposed the hole would have read as covered.
 *
 * Chromium's JS coverage answers the real question. Vite serves one script per
 * source file in dev, so per-function execution counts map straight onto
 * repository paths: on the deck route `RunLedgerReceipt.svelte` has one named
 * function and zero executed ranges, while `Dashboard.svelte` ran 93 of 133.
 * Imported-and-never-invoked is exactly the state the defect lived in, and it
 * is directly observable rather than inferred.
 *
 * One recorder per driver. `watch` before the first navigation, `harvest`
 * before the page or its context closes (the CDP session dies with it), `save`
 * beside the shots. Every step is non-fatal: a driver that loses its record
 * makes the comment say *coverage unknown*, never invent a verdict.
 */
export function exerciseRecorder({ root = process.cwd() } = {}) {
	const loaded = new Set();
	const exercised = new Set();
	const note = (label, message) => process.stderr.write(`[${label ?? 'coverage'}] ${message}\n`);

	// A module counts as exercised when a *named* function in it ran: the
	// module top level runs on mere import, so counting it would put every
	// imported component back in the covered set. A module with no named
	// functions at all — a table of constants — has no unexecuted behaviour to
	// miss, so its top level running is the whole story and counts.
	const ran = (entry) => {
		const named = (entry.functions ?? []).filter((fn) => fn.functionName);
		const pool = named.length ? named : (entry.functions ?? []);
		return pool.some((fn) => fn.ranges?.some((range) => range.count > 0));
	};

	return {
		/** Start recording on a page. Call before its first navigation. */
		async watch(page, label) {
			page.on('request', (request) => {
				const file = normalizeModule(request.url(), { root });
				if (file) loaded.add(file);
			});
			try {
				await page.coverage.startJSCoverage({ resetOnNavigation: false });
			} catch (err) {
				note(label, `js coverage unavailable: ${err?.message ?? err}`);
			}
			return page;
		},
		/** Collect a page's coverage. Call before the page or its context closes. */
		async harvest(page, label) {
			try {
				for (const entry of await page.coverage.stopJSCoverage()) {
					const file = normalizeModule(entry.url, { root });
					if (!file) continue;
					loaded.add(file);
					if (ran(entry)) exercised.add(file);
				}
			} catch (err) {
				note(label, `js coverage not harvested: ${err?.message ?? err}`);
			}
		},
		record() {
			return { exercised: [...exercised].sort(), loaded: [...loaded].sort() };
		},
		/** Write `modules.json` next to this driver's shots. Never throws. */
		async save(outDir, label) {
			try {
				await mkdir(outDir, { recursive: true });
				await writeFile(
					join(outDir, 'modules.json'),
					`${JSON.stringify(this.record(), null, 2)}\n`
				);
			} catch (err) {
				note(label, `module record not written: ${err?.message ?? err}`);
			}
		}
	};
}
