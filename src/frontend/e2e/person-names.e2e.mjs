import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

test('a display name labels the header, wish letters, ledger and bead authors', async () => {
	const parts = fixture();
	parts.index.person_names = { ada: 'Ada Lovelace' };
	const B = await open({ parts: reindex(parts) });
	try {
		assert.match(await B.page.locator('#at').innerText(), /as Ada Lovelace/);
		const card = B.page.locator('.wc[data-wish="w-alpha0000000"]');
		await card.locator('.wch').click();
		assert.match(await card.locator('.lrow').first().innerText(), /Ada Lovelace/);
		const ledger = B.page.locator('[data-column="alpha"] .lg-row[data-wish="w-alpha0000000"]');
		await ledger.locator('.lg-h').click();
		assert.match(await ledger.locator('.lrow').first().innerText(), /Ada Lovelace/);
		await B.page.addInitScript(() => {
			localStorage.setItem('board.letterview', '["inbox"]');
			localStorage.setItem('board.place', JSON.stringify({ bead: 'letter:mine:100' }));
		});
		await B.page.reload();
		await B.page.waitForSelector('.sheet');
		assert.match(await B.page.locator('.sheet').innerText(), /Ada Lovelace wrote/);
		const letter = B.page.locator('[data-column="inbox"] [data-letter="letter:mine:100"]');
		assert.match(await letter.innerText(), /Ada Lovelace/);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});
