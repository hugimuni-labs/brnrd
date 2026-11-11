// The header's items never draw over each other (w-6cqovdrttk6j, his 10403): on a phone the fuel gauges shrank
// under min-width: 0 and ran over the open count; each item keeps its own width and the header scrolls instead.
import { after, before, test } from 'node:test';
import { deepEqual, equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

// the live header's four harnesses, each with a reset dial: the width that ran over the open count
const soon = new Date(Date.now() + 53 * 60_000).toISOString();
const g = (window, left_pct) => ({
	window,
	left_pct,
	resets_at: window === 'week' ? new Date(Date.now() + 3.5 * 86400000).toISOString() : soon
});
const parts = fixture();
const four = reindex({
	...parts,
	index: {
		...parts.index,
		fuel: [
			{ shell: 'claude', gauges: [g('session', 10), g('week', 39)] },
			{ shell: 'codex', gauges: [g('session', 93), g('week', 98)] },
			{ shell: 'grok', gauges: [g('week', 32)] },
			{ shell: 'vibe', error: 'fails' }
		]
	}
});

let B;
before(async () => (B = await open({ parts: four, viewport: { width: 390, height: 844 } })));
after(() => B?.close());

test('on a phone, no header item overlaps the next', async () => {
	const boxes = await B.page.evaluate(() =>
		[...document.querySelectorAll('.brd header > *')]
			.filter((e) => e.offsetParent)
			.map((e) => {
				const r = e.getBoundingClientRect();
				return {
					id: e.id || e.tagName,
					left: r.left,
					right: r.right,
					width: r.width,
					inner: e.scrollWidth
				};
			})
	);
	ok(boxes.length > 1);
	for (let i = 1; i < boxes.length; i++)
		ok(boxes[i].left >= boxes[i - 1].right - 0.5, `${boxes[i - 1].id} runs over ${boxes[i].id}`);
	for (const b of boxes) ok(b.inner <= Math.ceil(b.width) + 1, `${b.id} overflows its own box`);
	deepEqual(B.errors, []);
});

test('the coloured fill and reset wedge follow the 5h bar, or the week when alone', async () => {
	const read = () =>
		B.page.locator('#fuel .shell').evaluateAll((shells) =>
			shells.map((shell) => {
				const track = shell.querySelector('.fuel-track');
				const fill = shell.querySelector('.fuel-fill');
				const wedge = shell.querySelector('.fuel-dial circle[stroke-dasharray]');
				const dash = wedge?.getAttribute('stroke-dasharray').split(' ').map(Number);
				return {
					bar: shell.dataset.bar,
					width: shell.getBoundingClientRect().width,
					fill: fill ? getComputedStyle(fill).backgroundColor : null,
					track: track ? getComputedStyle(track).backgroundColor : null,
					fraction: dash ? dash[0] / dash[1] : null,
					when: shell.querySelector('.fuel-when')?.textContent,
					week: shell.querySelector('.fuel-side')?.textContent.trim(),
					marks: shell.querySelectorAll('.fuel-dot, .fuel-tick').length
				};
			})
		);
	const before = await read();
	deepEqual(
		before.map((s) => s.bar),
		['session', 'session', 'week', '']
	);
	deepEqual(
		before.map((s) => s.fill),
		['rgb(156, 141, 125)', 'rgb(232, 179, 74)', 'rgb(168, 203, 219)', null]
	);
	for (const s of before.slice(0, 3)) {
		equal(s.track, 'rgb(230, 230, 230)');
		equal(s.marks, 0);
		ok(s.when.startsWith('resets in '));
	}
	ok(Math.abs(before[0].fraction - 53 / 300) < 0.01);
	ok(Math.abs(before[2].fraction - 0.5) < 0.01);
	equal(before[0].week, 'week 39%');
	equal(before[1].week, 'week 98%');
	equal(before[2].week, undefined);
	equal(before[3].fraction, null);
	await B.page.clock.install();
	await B.page.clock.fastForward(3600000);
	await B.page.waitForFunction(
		() => document.querySelector('#fuel .fuel-when')?.textContent === 'resets now'
	);
	const after = await read();
	deepEqual(
		after.map((s) => s.width),
		before.map((s) => s.width)
	);
	equal(after[0].fraction, 0);
	ok(after[2].fraction < before[2].fraction);
	deepEqual(B.errors, []);
});
