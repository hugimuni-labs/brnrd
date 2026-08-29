import assert from 'node:assert/strict';
import test from 'node:test';

import {
	recordCommands,
	terminalBox,
	terminalFeed,
	TERMINAL_CAP,
	TERMINAL_COLS,
	TERMINAL_ROWS,
	type TerminalLine
} from './roomTerminal.ts';
import type { LiveRun } from './liveRuns.ts';

const edge = (at: string, act: string, detail: string) =>
	({
		run_id: 'run-1',
		edge: { at, phase: 'post-tool', act, tools: ['Bash'], detail, out_bytes: 1, injected: false }
	}) as unknown as Pick<LiveRun, 'run_id' | 'edge'>;

// The terminal records the *boundary*, not the footstep. The trail drops any
// boundary whose directory will not resolve (`if (!dir || !at) continue`),
// which is correct for terrain and would be a silent narrowing here — a
// command log omitting every command run from an unresolvable cwd.
test('every attested boundary is a command, not only the ones that moved', () => {
	const store: Record<string, TerminalLine[]> = {};
	const fresh = recordCommands(
		[
			edge('2026-08-28T22:00:00Z', 'mutate', "python3 - <<'PY' …"),
			// a second run's boundary keeps its own scrollback
			{
				run_id: 'run-2',
				edge: {
					at: '2026-08-28T22:00:01Z',
					phase: 'post-tool',
					act: 'orient',
					tools: ['Bash'],
					detail: 'ls',
					out_bytes: 1,
					injected: false
				}
			} as unknown as Pick<LiveRun, 'run_id' | 'edge'>
		],
		store
	);
	assert.equal(fresh.length, 2);
	assert.equal(store['run-1'].length, 1);
	assert.equal(store['run-2'].length, 1);
});

test('a boundary is recorded once, keyed by its timestamp', () => {
	const store: Record<string, TerminalLine[]> = {};
	recordCommands([edge('2026-08-28T22:00:00Z', 'mutate', 'a')], store);
	const again = recordCommands([edge('2026-08-28T22:00:00Z', 'mutate', 'a')], store);
	assert.equal(again.length, 0, 'a poll that caught the same edge adds nothing');
	assert.equal(store['run-1'].length, 1);
});

test('the scrollback is bounded and keeps the newest', () => {
	const store: Record<string, TerminalLine[]> = {};
	for (let i = 0; i < TERMINAL_CAP + 10; i++) {
		recordCommands(
			[edge(`2026-08-28T22:${String(i).padStart(2, '0')}:00Z`, 'mutate', `c${i}`)],
			store
		);
	}
	assert.equal(store['run-1'].length, TERMINAL_CAP);
	assert.equal(store['run-1'].at(-1)?.detail, `c${TERMINAL_CAP + 9}`);
});

// The pager needed this once `✉×151 read` turned out to be counting runs
// that had ended days ago.
test('the feed scopes to live runs, newest first', () => {
	const store: Record<string, TerminalLine[]> = {};
	recordCommands([edge('2026-08-28T22:00:00Z', 'mutate', 'first')], store);
	recordCommands([edge('2026-08-28T22:01:00Z', 'mutate', 'second')], store);
	assert.deepEqual(
		terminalFeed(store, 'run-1', ['run-1']).map((l) => l.detail),
		['second', 'first']
	);
	assert.deepEqual(terminalFeed(store, 'run-1', ['run-other']), [], 'a dead run shows nothing');
});

// "a few lines in height, about 50 in width" — and a terminal has a floor,
// so it bounds itself. Running out of room has to be *legible*, which is the
// whole difference from the feed this replaces.
test('the window is exactly its declared size, whatever it is given', () => {
	const many: TerminalLine[] = Array.from({ length: 30 }, (_, i) => ({
		at: `2026-08-28T22:${String(i).padStart(2, '0')}:00Z`,
		act: 'mutate',
		detail: 'x'.repeat(200)
	}));
	const box = terminalBox(many);
	assert.equal(box.length, TERMINAL_ROWS + 2, 'body rows plus the frame, never more');
	for (const row of box) assert.equal(row.length, TERMINAL_COLS, `row is ${TERMINAL_COLS} wide`);
	assert.ok(
		box.some((r) => r.includes('…')),
		'an overrunning command says so rather than being silently cut'
	);
	assert.ok(box.at(-1)?.includes('25 older'), 'and the floor names what it could not show');
});

// An empty window and a broken one must not look alike — the defect this
// whole round has been about, in miniature.
test('an empty terminal says it is empty, and still holds its shape', () => {
	const box = terminalBox([]);
	assert.equal(box.length, TERMINAL_ROWS + 2);
	assert.ok(box.some((r) => r.includes('no commands yet')));
	assert.ok(!box.at(-1)?.includes('older'), 'nothing hidden, so no overflow claim');
});

test('the box is pure — the same lines render the same window', () => {
	const lines: TerminalLine[] = [{ at: '2026-08-28T22:00:00Z', act: 'mutate', detail: 'npm test' }];
	assert.deepEqual(
		terminalBox(lines),
		terminalBox(lines),
		'clock-free, so the flash diff can ride it'
	);
});
