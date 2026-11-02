// Every link out of the app opens a new tab (w-lmxpqx5558uf, his 10059): a granting wish's `owner/repo#N` ref is a
// forge link, and a click on it, or on any outbound anchor without a target of its own, leaves the board where it is.
import { after, before, test } from 'node:test';
import { equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const PR = 'hugimuni-labs/brnrd#2584',
	URL_ = 'https://github.com/hugimuni-labs/brnrd/pull/2584';

const home = () => {
	const parts = fixture();
	const w = Object.values(parts)
		.flatMap((p) => p?.wishes || [])
		.find((x) => x.state === 'granting');
	ok(w, 'the fixture has a granting wish');
	w.results.push({ ref: PR, kind: 'pr', line: 'the change', at: at(30) });
	return reindex(parts);
};

let B;
before(async () => {
	B = await open({ parts: home() });
	await B.page
		.context()
		.route(/^https:\/\/(github\.com|example\.com)\//, (r) => r.fulfill({ body: 'forge' }));
});
after(() => B?.close());

const opensTab = async (sel) => {
	const was = B.page.url();
	const [tab] = await Promise.all([
		B.page.context().waitForEvent('page', { timeout: 10_000 }),
		B.page.locator(sel).first().click()
	]);
	equal(B.page.url(), was, 'the board stays where it was');
	return tab;
};

test("a granting wish's PR ref is a link, and it opens a new tab", async () => {
	const a = B.page.locator(`.brd a[href="${URL_}"]`);
	ok((await a.count()) > 0, 'the PR ref renders as a forge link');
	const tab = await opensTab(`.brd a[href="${URL_}"]`);
	await tab.waitForLoadState();
	equal(tab.url(), URL_);
	await tab.close();
});

test('an outbound anchor with no target of its own opens a new tab too (the layout rule)', async () => {
	await B.page.evaluate(() => {
		const a = document.createElement('a');
		a.id = 'bare-out';
		a.href = 'https://example.com/x';
		a.textContent = 'out';
		a.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;background:#fff';
		document.body.append(a);
	});
	const tab = await opensTab('#bare-out');
	await tab.close();
	equal(B.errors.length, 0, B.errors.join('\n'));
});
