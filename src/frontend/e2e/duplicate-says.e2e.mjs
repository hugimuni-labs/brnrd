import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium, webkit } from 'playwright';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

const wish = 'w-alpha0000000';
for (const browserType of [chromium, webkit]) {
	test(`${browserType.name()}: saved disposition with repeated wish annotations loads`, async () => {
		const parts = fixture();
		const l = parts['thread/inbox'].letters.find((l) => l.id === 'letter:mine:100');
		const w = parts['thread/alpha'].wishes[0];
		l.wishes = [w, { ...w }];
		const B = await open({ parts: reindex(parts), browserType });
		try {
			await B.page.addInitScript(() => {
				localStorage.setItem('board.letterview', '["inbox"]');
				localStorage.setItem('board.place', JSON.stringify({ open: ['d:letter:mine:100'] }));
			});
			await B.page.reload();
			const letter = B.page.locator('[data-column="inbox"] [data-letter="letter:mine:100"]');
			await letter.locator('.dissect').waitFor();
			assert.equal(await letter.locator('.dissect > .row').count(), 1);
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	});
}

for (const browserType of [chromium, webkit]) {
	for (const view of ['card', 'ledger', 'restart']) {
		test(`${browserType.name()}: duplicate says in ${view}`, async () => {
			const parts = fixture();
			const w = parts['thread/alpha'].wishes[0];
			w.says.push({ ...w.says[0] });
			w.letters.push(w.letters[0]);
			const B = await open({ parts: reindex(parts), browserType });
			try {
				const selector =
					view === 'card'
						? `.wc[data-wish="${wish}"]`
						: `[data-column="alpha"] .lg-row[data-wish="${wish}"]`;
				if (view === 'restart') {
					await B.page.addInitScript(
						(wish) =>
							localStorage.setItem(
								'board.place',
								JSON.stringify({ open: [`w:alpha|${wish}`], bead: wish })
							),
						wish
					);
					await B.page.reload();
				} else {
					await B.page.locator(`${selector} ${view === 'card' ? '.wch' : '.lg-h'}`).click();
				}
				await B.page.waitForSelector(`${selector} .say`);
				assert.equal(await B.page.locator(`${selector} .say`).count(), 2);
				assert.deepEqual(B.errors, []);
			} finally {
				await B.close();
			}
		});
	}
}

for (const browserType of [chromium, webkit]) {
	test(`${browserType.name()}: render recovery clears a poisoned place once`, async () => {
		const B = await open({ browserType });
		const logged = [];
		B.page.on('console', (m) => {
			if (m.type() === 'error' && m.text().startsWith('[board render]')) logged.push(m.text());
		});
		try {
			// An invalid result timestamp throws only inside the open ledger; inbox cards stay hidden.
			const parts = fixture();
			parts['thread/alpha'].wishes[0].results = [{ ref: 'bad-time', at: 42 }];
			B.loom.set(reindex(parts));
			await B.page.addInitScript((wish) => {
				localStorage.setItem('board.letterview', '["inbox"]');
				if (!sessionStorage.getItem('board.recovered'))
					localStorage.setItem('board.place', JSON.stringify({ open: [`w:alpha|${wish}`] }));
			}, wish);
			await B.page.reload();
			await B.page.waitForFunction(
				() =>
					sessionStorage.getItem('board.recovered') === '1' &&
					!!document.querySelector('.brd .column.threadcol'),
				null,
				{ timeout: 20000 }
			);
			const p = await B.page.evaluate(() => JSON.parse(localStorage.getItem('board.place')));
			assert.deepEqual(p.open, []);
			assert.equal(p.bead, null);
			assert.equal(await B.page.locator('[data-board-error]').count(), 0);
			// Now a failure independent of open rows: the same tab must show the fallback, not reload forever.
			await B.page.goto(B.base);
			await B.page.waitForSelector('.brd .column.threadcol');
			await B.page.locator('#say').fill('keep this draft through a render failure');
			parts['thread/alpha'].wishes[0].stamps = null;
			B.loom.set(reindex(parts));
			await B.page.evaluate(() => window.__board.load());
			await B.page.waitForSelector('[data-board-error]', { timeout: 20000 });
			assert.equal(
				await B.page.evaluate(() => JSON.parse(sessionStorage.getItem('board.draft')).body),
				'keep this draft through a render failure'
			);
			let loads = 0;
			B.page.on('request', (r) => {
				if (r.isNavigationRequest() && r.frame() === B.page.mainFrame()) loads++;
			});
			await B.page.reload();
			await B.page.waitForSelector('[data-board-error]', { timeout: 20000 });
			await B.page.waitForTimeout(300);
			assert.equal(loads, 1);
			assert.equal(await B.page.getByRole('button', { name: 'Reload', exact: true }).count(), 1);
			assert.ok(logged.length > 0, 'the boundary logs its original error');
			assert.ok(
				B.errors.some((e) => e.includes('[board-render-error]')),
				'the harness counts a rendered fallback'
			);
			assert.ok((await B.page.locator('[data-board-error] code').textContent()).length > 0);
		} finally {
			await B.close();
		}
	});
}

for (const browserType of [chromium, webkit]) {
	test(`${browserType.name()}: repeated topic citations and fuel shells render`, async () => {
		const parts = fixture();
		parts.topics[0].cites.push(parts.topics[0].cites[0]);
		parts.index.fuel.push({ ...parts.index.fuel[0] });
		const B = await open({ parts: reindex(parts), browserType });
		try {
			await B.page.goto(B.base + 'topics');
			await B.page.locator('[data-topic="board"]').click();
			assert.equal(await B.page.locator('[data-topic="board"] .topic-cites button').count(), 2);
			assert.equal(await B.page.locator('#fuel [data-shell="claude"]').count(), 1);
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	});
}
