// A wish's bead sheet (board.html's renderBead, live-board): its five stages as one rail in pip order, times that drop a
// repeated day, the stage that waits on him ringed; then the trace by time, where a stamp's ref opens what it names (a
// PR to the forge, a ledger id to its bead, a receipt to its Plan step), a result no stamp named, and the verdict.
import { after, before, test } from 'node:test';
import { deepEqual, equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const BRAVO = 'w-bravo0000000',
	FOX = 'w-foxtrot0000000',
	NOTE = 's-test-brav1/note';

// bravo (delivered, waits on his verdict): its stamps carry refs the way the loom writes them
const home = () => {
	const parts = fixture(),
		w = parts['thread/bravo'].wishes[0],
		ref = { read: 'letter:mine:101', granting: 'brnrd#2461', delivered: 'abc1234' };
	for (const s of w.stamps) if (ref[s.stamp]) s.ref = ref[s.stamp];
	w.stamps.find((s) => s.stamp === 'delivered').line = 'the parser is out';
	// a letter hung from the wish between two of its stamps: the trace interleaves it by time
	parts.beads[NOTE] = [BRAVO, 'link', 's-test-brav1', at(9), 'a note on bravo'];
	return reindex(parts);
};

let B;
before(async () => (B = await open({ parts: home() })));
after(() => B?.close());

const sheet = async (wish) => {
	if (await B.page.locator('#bead.on').count()) await B.page.keyboard.press('Escape');
	await B.page.locator(`.brd [data-pip="${wish}"]`).first().click();
	await B.page.waitForSelector(`#bead.on .bd.me[data-bead="${wish}"]`);
	return B.page.evaluate(() => ({
		rail: [...document.querySelectorAll('#bead .rail .bd.stage')].map((s) => ({
			k: s.dataset.stage,
			not: s.classList.contains('not'),
			wait: s.classList.contains('waitv'),
			when: s.querySelector('em').textContent.trim()
		})),
		down: [...document.querySelectorAll('#bead .down .bd')].map(
			(r) => r.dataset.trace || r.dataset.bead
		),
		trace: [...document.querySelectorAll('#bead .down .bd.trace')].map((r) => ({
			k: r.dataset.trace,
			vb: r.querySelector('.vb').textContent.trim(),
			href: r.querySelector('.x a')?.getAttribute('href') || null,
			bead: r.querySelector('.x .ref')?.dataset.bead || null,
			sha: r.querySelector('.x .sha')?.textContent || null,
			x: [...r.querySelectorAll('.x')].map((x) => x.textContent.trim())
		}))
	}));
};

test('a waiting wish: five stages in pip order, the day said once, the verdict stage ringed', async () => {
	const { rail } = await sheet(BRAVO);
	deepEqual(
		rail.map((s) => s.k),
		['heard', 'read', 'granting', 'delivered', 'agreed']
	);
	deepEqual(
		rail.map((s) => s.not),
		[false, false, false, false, true]
	);
	deepEqual(
		rail.map((s) => s.wait),
		[false, false, false, false, true]
	);
	deepEqual(
		rail.map((s) => s.when),
		[
			`10-27 ${at(6).slice(11, 16)}`,
			at(7).slice(11, 16),
			at(8).slice(11, 16),
			at(10).slice(11, 16),
			'verdict'
		]
	);
});

test('its trace by time: each ref opens what it names, and the result no stamp named', async () => {
	const { down, trace } = await sheet(BRAVO);
	deepEqual(down, ['thread:bravo', 'read', 'granting', NOTE, 'delivered', 'result']);
	equal(trace[0].bead, 'letter:mine:101');
	equal(trace[1].href, 'https://github.com/hugimuni-labs/brnrd/pull/2461');
	equal(trace[2].sha, 'abc1234');
	deepEqual(trace[2].x, ['abc1234 write the parser', 'the parser is out']);
	equal(trace[3].x[0], 'threads/bravo/README.md');
	// a ledger ref re-centres the sheet on its bead
	await B.page.locator('#bead .down .bd.trace .x .ref').first().click();
	await B.page.waitForSelector('#bead.on .bd.me[data-bead="letter:mine:101"]');
	equal(await B.page.locator('#bead .rail').count(), 0, 'a letter has no stage rail');
});

test('an agreed wish: every stage filled, nothing ringed, the verdict in its trace', async () => {
	const { rail, trace } = await sheet(FOX);
	ok(rail.every((s) => !s.not && !s.wait));
	equal(trace.at(-1).vb, 'verdict · accepted');
	deepEqual(B.errors, []);
});
