import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture } from './fixture.mjs';

const loopback = { deployment: 'loopback', tree: null, features: [] };
const cloud = {
	deployment: 'cloud',
	tree: null,
	features: ['account', 'consent', 'dashboard', 'settings']
};
const unwanted = (B) =>
	B.requests.filter((p) => p === '/v1/loom/consent' || p.startsWith('/v1/dashboard/'));

test('loopback keeps the board while parts are missing and never asks for cloud UI', async () => {
	const B = await open({ site: loopback, parts: {}, ready: '.brd' });
	try {
		assert.equal(await B.page.locator('[data-home], [data-landing], [data-consent]').count(), 0);
		assert.deepEqual(unwanted(B), []);
		assert.equal(B.requests.filter((p) => p === '/v1/site').length, 1);
		B.loom.set(fixture());
		await B.page.waitForSelector('.brd .column.threadcol');
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('loopback sends cloud routes to the same path on brnrd.dev', async () => {
	const B = await open({ site: loopback });
	try {
		for (const path of [
			'/dashboard',
			'/dashboard/runs/a?tab=log',
			'/settings',
			'/login',
			'/connect',
			'/new',
			'/config-approve'
		]) {
			await B.page.goto(new URL(path, B.base).href);
			await B.page.waitForSelector('[data-cloud-page]');
			assert.match(
				await B.page.locator('[data-cloud-page]').innerText(),
				/This page lives on brnrd.dev/
			);
			assert.equal(
				await B.page.locator('[data-cloud-page] a').getAttribute('href'),
				`https://brnrd.dev${path}`
			);
		}
		assert.deepEqual(unwanted(B), []);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('cloud keeps consent; an unavailable declaration keeps the previous flow', async () => {
	for (const site of [cloud, null]) {
		const B = await open({
			site,
			parts: {},
			consent: { state: 'ask' },
			ready: '[data-consent="ask"]'
		});
		try {
			assert.equal(await B.page.locator('[data-home]').count(), 0);
			assert.ok(B.requests.includes('/v1/loom/consent'));
			assert.equal(B.requests.filter((p) => p === '/v1/site').length, 1);
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	}
});

test('the page shows its own stamp and offers a reload for a different served build', async () => {
	const B = await open({ site: { ...loopback, tree: 'another-build' } });
	try {
		assert.ok((await B.page.locator('[data-page-version]').innerText()).length > 0);
		const reload = B.page.getByRole('link', { name: 'newer page on the server, reload' });
		await reload.click();
		await B.page.waitForSelector('.brd .column.threadcol');
		assert.ok(B.requests.filter((p) => p === '/v1/site').length >= 2);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('both doors paint cached parts before a delayed site declaration answers', async () => {
	for (const site of [loopback, cloud]) {
		const B = await open({ site });
		let answered = false;
		let release;
		const delayed = new Promise((resolve) => {
			release = resolve;
		});
		try {
			await B.page.waitForFunction(() => localStorage.getItem('board.cached') === '1');
			await B.page.route('**/v1/site', async (route) => {
				await delayed;
				await route.fulfill({ json: site });
				answered = true;
			});
			await B.page.reload();
			await B.page.waitForSelector('.brd .column.threadcol');
			assert.equal(answered, false, 'the board painted while the declaration is still held');
			const response = B.page.waitForResponse('**/v1/site');
			release();
			await response;
			assert.deepEqual(unwanted(B), []);
			assert.deepEqual(B.errors, []);
		} finally {
			release();
			await B.close();
		}
	}
});
