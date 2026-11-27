// A page that leaves in the middle of a sync must not leave the next page a torn cache: the new index over the old
// parts it names. The next page would trust the index's hashes, fetch nothing, and keep showing the old thread until it
// moved again (chromium-recovery, 2026-11-27: render recovery in duplicate-says.e2e.mjs never saw its poisoned part).
// Chromium only: WebKit request interception clears Cache API storage.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

test('chromium: a reload in the middle of a sync shows the new part, not the cached one', async () => {
	const B = await open();
	try {
		const line = '[data-column="alpha"] .lg-row[data-wish="w-alpha0000000"] .lg-line';
		const was = await B.page.locator(line).textContent();
		let asked;
		const inFlight = new Promise((r) => (asked = r));
		// the new alpha part never answers this page: the reload is what ends its sync
		await B.page.route('**/v1/loom/thread/alpha', () => asked());
		const parts = fixture();
		parts['thread/alpha'].wishes[0].line = 'alpha after the reload';
		B.loom.set(reindex(parts));
		await inFlight;
		// Cache operations run in order: this read answers after any put the sync started before the part was asked.
		await B.page.evaluate(async () => (await caches.open('brnrd-board')).match('/v1/loom/index'));
		await B.page.unroute('**/v1/loom/thread/alpha');
		await B.page.reload();
		await B.page.waitForFunction(
			({ line, was }) => {
				const t = document.querySelector(line)?.textContent;
				return !!t && t !== was;
			},
			{ line, was },
			{ timeout: 10000 }
		);
		assert.match(await B.page.locator(line).textContent(), /alpha after the reload/);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});
