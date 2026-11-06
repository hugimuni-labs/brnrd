// The header's items never draw over each other (w-6cqovdrttk6j, his 10403): on a phone the fuel gauges shrank
// under min-width: 0 and ran over the open count; each item keeps its own width and the header scrolls instead.
import { after, before, test } from 'node:test';
import { deepEqual, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

// the live header's four harnesses, each with a reset dial: the width that ran over the open count
const soon = new Date(Date.now() + 53 * 60_000).toISOString();
const g = (window, left_pct) => ({ window, left_pct, resets_at: soon });
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
