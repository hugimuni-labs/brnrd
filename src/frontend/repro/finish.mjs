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
