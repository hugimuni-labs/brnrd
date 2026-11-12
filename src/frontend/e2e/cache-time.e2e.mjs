import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium, webkit } from 'playwright';
import { open } from './harness.mjs';

async function cacheReady(page) {
	await page.waitForFunction(async () => {
		const c = await caches.open('brnrd-board');
		const r = await c.match('/v1/loom/index');
		if (!r) return false;
		const index = await r.json();
		const names = Object.keys(index.parts).filter((n) => n.startsWith('thread/'));
		return (await Promise.all(names.map((n) => c.match('/v1/loom/' + n)))).every(Boolean);
	});
}

const cloud = { deployment: 'cloud', tree: null, features: ['account', 'consent'] };
for (const browserType of [chromium, webkit]) {
	for (const broken of ['letter', 'tool', 'live', 'paused']) {
		test(`${browserType.name()}: cache restore tolerates an undated ${broken} before the network`, async () => {
			const B = await open({
				browserType,
				persistent: true,
				site: cloud,
				viewport: { width: 390, height: 844 }
			});
			const release = B.loom.holdIndex();
			try {
				await cacheReady(B.page);
				await B.page.evaluate(async (broken) => {
					const c = await caches.open('brnrd-board');
					const p = await (await c.match('/v1/loom/thread/alpha')).json();
					if (broken === 'letter') {
						p.letters.push({
							id: 'undated',
							from: 'person:ada',
							body: 'undated cached letter',
							threads: ['alpha']
						});
					} else if (broken === 'tool') {
						p.tools[p.strands[0].id] = [
							{ tool: 'shell', end: '2026-10-27T09:05:00Z', cmd: 'undated call' }
						];
					} else if (broken === 'paused') {
						// Exact wire shape from Tools on main and before #2684: bool pause, no paused_at.
						p.live[p.strands[0].id] = { paused: true, open: [] };
					} else {
						p.live[p.strands[0].id] = { open: [{ tool: 'shell', cmd: 'undated open call' }] };
					}
					await c.put('/v1/loom/thread/alpha', new Response(JSON.stringify(p)));
					localStorage.setItem('board.letterview', '["inbox"]');
				}, broken);
				await B.page.goto('about:blank');
				await B.page.goto(B.base);
				await B.page.waitForSelector('.brd .column.threadcol', { timeout: 5000 });
				assert.deepEqual(B.errors, []);
				assert.doesNotMatch(await B.page.locator('.brd').innerText(), /NaN/);
				assert.equal(await B.page.evaluate(() => window.__board.model.byId.has('undated')), false);
				release();
				await B.page.waitForFunction(() => !!window.__board.heard);
				assert.deepEqual(B.errors, []);
			} finally {
				release();
				await B.close();
			}
		});
	}

	test(`${browserType.name()}: render recovery evicts a poisoned cache without a saved inspector`, async () => {
		const B = await open({ browserType, persistent: true, site: cloud });
		const release = B.loom.holdIndex();
		const logged = [];
		B.page.on('console', (m) => {
			if (m.type() === 'error' && m.text().startsWith('[board render]')) logged.push(m.text());
		});
		try {
			await cacheReady(B.page);
			await B.page.locator('#say').fill('keep my draft');
			await B.page.evaluate(async () => {
				const c = await caches.open('brnrd-board');
				const p = await (await c.match('/v1/loom/thread/alpha')).json();
				p.wishes[0].stamps = null;
				await c.put('/v1/loom/thread/alpha', new Response(JSON.stringify(p)));
				localStorage.removeItem('board.place');
			});
			// On the recovery navigation the cache and synchronous flag must already be gone.
			await B.page.addInitScript(() => {
				if (sessionStorage.getItem('board.recovered')) {
					window.__evicted = {
						flag: localStorage.getItem('board.cached'),
						cache: caches.has('brnrd-board')
					};
				}
			});
			await B.page.goto('about:blank');
			await B.page.goto(B.base);
			await B.page.waitForFunction(() => !!window.__evicted, null, { timeout: 10000 });
			assert.deepEqual(
				await B.page.evaluate(async () => ({
					flag: window.__evicted.flag,
					cache: await window.__evicted.cache
				})),
				{ flag: null, cache: false }
			);
			release();
			await B.page.waitForSelector('.brd .column.threadcol');
			assert.equal(await B.page.locator('#say').inputValue(), 'keep my draft');
			assert.ok(
				logged.some((s) => s.includes('Ledger') || s.includes('WishCard')),
				`component absent: ${logged}`
			);
			assert.equal(await B.page.locator('[data-board-error]').count(), 0);
		} finally {
			release();
			await B.close();
		}
	});

	test(`${browserType.name()}: an unechoed pending letter without at cannot blank the board`, async () => {
		const B = await open({ browserType });
		try {
			await B.page.evaluate(() => {
				window.__board.pending = [
					{
						id: 'pending-undated',
						body: 'pending undated',
						from: 'person:ada',
						person: true,
						threads: ['inbox'],
						pendingNote: true
					}
				];
			});
			await B.page.waitForTimeout(150);
			assert.deepEqual(B.errors, []);
			assert.equal((await B.page.locator('.brd .column.threadcol').count()) > 0, true);
		} finally {
			await B.close();
		}
	});
}
