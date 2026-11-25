// A page that leaves cancels its board reads (webkit-ci). A cancelled read that nobody awaits is still no pageerror:
// the beads part a press starts is in flight when the reload comes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webkit } from 'playwright';
import { open } from './harness.mjs';

for (const [name, browserType] of [
	['chromium', undefined],
	['webkit', webkit]
]) {
	test(`${name}: a reload with the beads part in flight raises no pageerror`, async () => {
		const B = await open({ browserType });
		try {
			let asked;
			const held = new Promise((r) => (asked = r));
			// the part never answers: the reload is what ends it
			await B.page.route('**/v1/loom/beads', () => asked());
			await B.page
				.locator('[data-column="alpha"] .lg-row[data-wish="w-alpha0000000"] .lg-h')
				.click();
			await held;
			await B.page.reload();
			await B.page.waitForSelector('.brd .column.threadcol');
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	});
}
