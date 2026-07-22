import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { type ScheduledWake, wakeTimingExplanation, wakeTimingText } from './scheduledWakes.ts';

const NOW = Date.parse('2026-07-22T10:00:00Z');

function wake(status: string, scheduledFor: string | null): ScheduledWake {
	return {
		id: 'schedule:upkeep',
		kind: 'scheduled',
		source: 'schedule',
		status,
		phase: 'every',
		bucket: 'scheduled',
		summary: 'self-scheduled thought: upkeep',
		repo_label: null,
		daemon_name: null,
		conversation_key: 'schedule:upkeep',
		scheduled_for: scheduledFor,
		reported_at: null
	};
}

describe('scheduler pacing truth', () => {
	it('does not label a quota-paused wake overdue', () => {
		const paused = wake('quota-paused', '2026-07-22T09:00:00Z');
		assert.equal(wakeTimingText(paused, NOW), 'quota-paused');
		assert.match(wakeTimingExplanation(paused) ?? '', /reevaluated/);
	});

	it('labels the scheduler-provided stretched timestamp as effective', () => {
		const paced = wake('quota-paced', '2026-07-22T11:00:00Z');
		assert.equal(wakeTimingText(paced, NOW), 'quota-paced · in 1h 0m');
		assert.match(wakeTimingExplanation(paced) ?? '', /effective next fire/);
	});
});
