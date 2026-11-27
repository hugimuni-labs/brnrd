import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { residentClosed, residentText } from './parts.ts';
import type { Fuel, Presence } from './types.ts';

const fuel: Fuel[] = [
	{
		shell: 'claude',
		read_at: '2026-11-27T19:42:00Z',
		pace: 'session pace: dry in about 3h, before the reset',
		gauges: [
			{ window: 'session', left_pct: 33, resets_at: '2026-11-27T22:20:00Z' },
			{ window: 'week', left_pct: 96, resets_at: '2026-11-28T12:00:00Z' }
		]
	},
	{
		shell: 'grok',
		read_at: '2026-11-27T19:42:00Z',
		gauges: [{ window: 'week', left_pct: 50, resets_at: '2026-11-28T12:00:00Z' }]
	}
];

describe('resident row', () => {
	it('says a seated body is on the plan, not live work', () => {
		const presence: Presence = {
			state: 'working',
			body: 'claude/fable',
			shell: 'claude',
			since: '2026-11-27T17:00:00Z',
			now: 'Build the row',
			doing: 'plan',
			step: 'name the keys',
			letters: [{ id: 'p-test/one', filed: 'wish', wish: 'w-residentrow1', state: 'heard' }]
		};
		const lines = residentText(presence, fuel);
		assert.match(lines[0], /claude\/fable in the seat since 17:00Z/);
		assert.match(lines[1], /plan's next line, not live work: Build the row/);
		assert.match(lines[1], /The step still on that wish: name the keys/);
		assert.match(lines[2], /p-test\/one filed as w-residentrow1 heard/);
		assert.match(lines[3], /Session 33% left, resets 22:20Z/);
		assert.match(lines[3], /dry in about 3h, before the reset/);
		const closed = residentClosed(presence, fuel);
		assert.equal(
			closed.line,
			'claude/fable in the seat since 17:00Z · Build the row · dry in about 3h, before the reset'
		);
	});

	it('cuts a long plan and hides the pace unless the window runs dry before its reset', () => {
		const presence: Presence = {
			state: 'working',
			body: 'claude/fable',
			shell: 'claude',
			since: '2026-11-27T17:00:00Z',
			now: 'Publish the resident row as one pull request'
		};
		const cut = residentClosed(presence, fuel);
		assert.equal(cut.plan, 'Publish the resident row as one…');
		assert.equal(cut.pace, 'dry in about 3h, before the reset');
		const after = residentClosed(presence, [
			{ shell: 'claude', gauges: [], pace: 'session pace: dry in about 3h, after the reset' }
		]);
		assert.equal(after.pace, '');
		assert.equal(
			after.line,
			'claude/fable in the seat since 17:00Z · Publish the resident row as one…'
		);
		const quiet = residentClosed(presence, [
			{ shell: 'claude', gauges: [], pace: 'session pace: not falling' }
		]);
		assert.equal(quiet.pace, '');
		assert.doesNotMatch(quiet.line, /pace|dry|reset/);
	});

	it('an empty seat with nothing pending says the next message', () => {
		const lines = residentText(
			{
				state: 'empty',
				last_body: 'codex',
				shell: 'codex',
				since: '2026-11-27T17:24:08Z',
				wait: 'starts on your next message'
			},
			[]
		);
		assert.equal(
			lines[0],
			'Seat empty since 17:24Z. Last engine codex. Starts on your next message.'
		);
		const closed = residentClosed(
			{
				state: 'empty',
				last_body: 'codex',
				shell: 'codex',
				since: '2026-11-27T17:24:08Z',
				wait: 'starts on your next message'
			},
			[]
		);
		assert.equal(closed.line, 'Seat empty since 17:24Z. Last engine codex');
		assert.doesNotMatch(closed.line, /your next message/);
	});

	it('an empty seat with a park does not say the next message', () => {
		const lines = residentText(
			{
				state: 'empty',
				last_body: 'codex',
				since: '2026-11-27T17:24:08Z',
				wait: 'wakes on a park on session until 2026-11-27T22:20:00Z'
			},
			fuel
		);
		assert.match(lines[0], /park on session/);
		assert.doesNotMatch(lines[0], /your next message/);
		const closed = residentClosed(
			{
				state: 'empty',
				last_body: 'codex',
				since: '2026-11-27T17:24:08Z',
				wait: 'wakes on a park on session until 2026-11-27T22:20:00Z'
			},
			fuel
		);
		assert.match(closed.line, /Seat empty since 17:24Z/);
		assert.doesNotMatch(closed.line, /your next message/);
	});

	it('an engine with no session window says so', () => {
		const lines = residentText({ state: 'working', body: 'grok', shell: 'grok' }, fuel);
		assert.match(lines[1], /No short window/);
		assert.match(lines[1], /Week 50% left/);
		const closed = residentClosed({ state: 'working', body: 'grok', shell: 'grok' }, fuel);
		assert.equal(closed.line, 'grok in the seat');
		assert.equal(closed.pace, '');
	});
});
