import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webkit } from 'playwright';
import { open } from './harness.mjs';

const cloud = { deployment: 'cloud', tree: null, features: ['account', 'consent'] };

// Fault injection checks recovery; no current publisher path producing these bodies is known.
for (const broken of ['shape', 'json']) {
	test(`a cloud board discards a cached part with bad ${broken} and fetches it again`, async () => {
		const B = await open({ site: cloud, browserType: webkit });
		try {
			await B.page.waitForFunction(async () => {
				const cache = await caches.open('brnrd-board');
				return !!(await cache.match('/v1/loom/thread/alpha'));
			});
			await B.page.evaluate(async (broken) => {
				const cache = await caches.open('brnrd-board');
				const response = await cache.match('/v1/loom/thread/alpha');
				const part = await response.json();
				delete part.letters;
				await cache.put(
					'/v1/loom/thread/alpha',
					new Response(broken === 'json' ? '{' : JSON.stringify(part))
				);
			}, broken);
			await B.page.route('**/v1/loom/index', async (route) => {
				await new Promise((resolve) => setTimeout(resolve, 500));
				await route.continue();
			});
			const n = B.requests.length;
			await B.page.reload();
			await B.page.waitForSelector('.brd .column.threadcol');
			assert.ok(B.requests.slice(n).includes('/v1/loom/thread/alpha'));
			await B.page.waitForFunction(async () => {
				const c = await caches.open('brnrd-board');
				const r = await c.match('/v1/loom/thread/alpha');
				if (!r) return false;
				const p = await r.json().catch(() => null);
				return Array.isArray(p?.letters);
			});
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	});
}

for (const method of ['put', 'match', 'open']) {
	test(`a cloud board loads with Cache API ${method} refused`, async () => {
		const B = await open({ site: cloud, browserType: webkit });
		try {
			await B.page.addInitScript((method) => {
				localStorage.setItem('board.cached', '1');
				const proto = method === 'open' ? CacheStorage.prototype : Cache.prototype;
				proto[method] = () => {
					throw new DOMException('Storage refused', 'QuotaExceededError');
				};
			}, method);
			await B.page.reload();
			await B.page.waitForSelector('.brd .column.threadcol');
			await B.page.waitForTimeout(200);
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	});
}

test('the board serves one current favicon and the matching touch icon', async () => {
	const B = await open({ site: cloud, browserType: webkit });
	try {
		assert.deepEqual(
			await B.page
				.locator('link[rel="icon"]')
				.evaluateAll((els) => els.map((el) => new URL(el.href).pathname)),
			['/brr-avatar.png']
		);
		assert.equal(
			new URL(await B.page.locator('link[rel="apple-touch-icon"]').getAttribute('href'), B.base)
				.pathname,
			'/brr-avatar.png'
		);
		const response = await B.page.request.get(new URL('/brr-avatar.png', B.base).href);
		assert.equal(response.status(), 200);
		assert.match(response.headers()['content-type'], /image\/png/);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('a cloud 401 thread response is never cached as a thread', async () => {
	const B = await open({ site: cloud, browserType: webkit });
	try {
		await B.page.evaluate(async () => {
			await caches.delete('brnrd-board');
			localStorage.removeItem('board.cached');
		});
		await B.page.route('**/v1/loom/thread/alpha', (route) =>
			route.fulfill({
				status: 401,
				contentType: 'application/json',
				body: '{"error":"unauthenticated"}'
			})
		);
		const response = B.page.waitForResponse(
			(r) => r.url().endsWith('/v1/loom/thread/alpha') && r.status() === 401
		);
		await B.page.reload();
		await response;
		await B.page.waitForTimeout(200);
		assert.equal(
			await B.page.evaluate(async () => {
				const c = await caches.open('brnrd-board');
				return !!(await c.match('/v1/loom/thread/alpha'));
			}),
			false
		);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});
