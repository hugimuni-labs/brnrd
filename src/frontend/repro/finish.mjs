// Bounded teardown for local browser repro drivers.

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
 * Flush stdout before forcing exit so a piped report is not truncated.
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
