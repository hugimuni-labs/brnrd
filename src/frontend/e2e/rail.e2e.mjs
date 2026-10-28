// The strand rail (board.html rail()): each live strand wears its face, the loom's frames played every 400 ms (running
// for a working strand); its live line ticks by the second; a strand whose state moves flashes and logs
// `HH:MM:SS thread was→now`, the last six kept.
import { after, before, test } from 'node:test';
import { deepEqual, equal, match, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

let B;
// the working strand's open call started seconds ago, so its live line reads in seconds and moves every tick
const home = () => {
	const parts = fixture();
	for (const p of Object.values(parts))
		for (const v of Object.values(p.live || {}))
			v.open[0] = { ...v.open[0], at: new Date(Date.now() - 5000).toISOString() };
	return reindex(parts);
};
before(async () => (B = await open({ parts: home() })));
after(() => B?.close());

const rail = () =>
	B.page.evaluate(() => ({
		spans: [...document.querySelectorAll('#rail span[data-strand]')].map((s) => ({
			thread: s.dataset.thread,
			state: s.querySelector('i').dataset.state,
			face: s.querySelector('i').textContent,
			em: s.querySelector('em')?.textContent || '',
			flash: s.classList.contains('flash')
		})),
		log: [...document.querySelectorAll('#rail .log')].map((s) => s.textContent)
	}));

test('a working strand plays the running frames and its live line ticks', async () => {
	const running = new Set(['brnrd', 'brvrd', 'br^rd']);
	const faces = new Set(),
		ems = new Set();
	for (let i = 0; i < 8; i++) {
		const w = (await rail()).spans.find((s) => s.state === 'working');
		ok(w, 'a working strand on the rail');
		faces.add(w.face);
		ems.add(w.em);
		await B.page.waitForTimeout(300);
	}
	ok(
		[...faces].every((f) => running.has(f)),
		[...faces].join()
	);
	ok(faces.size > 1, 'the face moves');
	ok(ems.size > 1, 'the live line ticks');
	match([...ems][0], / · running \d+/);
	for (const s of (await rail()).spans.filter((s) => s.state !== 'working'))
		ok(['brnrd', 'b-n-d'].includes(s.face), s.face);
	deepEqual((await rail()).log, [], 'the first load logs nothing');
});

test('a strand whose state moves flashes and logs was→now', async () => {
	const parts = fixture();
	const t = parts.index.threads.find((t) => t.strands.slice(-1)[0]?.state === 'working');
	const s = t.strands.slice(-1)[0];
	s.state = 'holding';
	// catch the flash the moment the span takes it: it lasts two frames
	await B.page.evaluate((id) => {
		window.__flashed = false;
		new MutationObserver((ms, o) => {
			if (!document.querySelector(`#rail span.flash[data-strand="${id}"]`)) return;
			window.__flashed = true;
			o.disconnect();
		}).observe(document.querySelector('#rail'), {
			subtree: true,
			attributes: true,
			childList: true
		});
	}, s.id);
	B.loom.set(reindex(parts));
	await B.page.waitForSelector('#rail .log');
	const r = await rail();
	equal(r.log.length, 1);
	match(r.log[0], new RegExp(`^\\d\\d:\\d\\d:\\d\\d ${t.id} working→holding$`));
	ok(await B.page.evaluate(() => window.__flashed), 'the moved strand flashed');
	ok(
		['brnrd', 'b-n-d'].includes(r.spans.find((x) => x.thread === t.id).face),
		'a holding strand rests'
	);
});
