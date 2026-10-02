import test from 'node:test';
import assert from 'node:assert/strict';
import { startVisiblePoll } from './visiblePoll.ts';

class Visibility extends EventTarget {
	hidden = false;
	change(hidden: boolean) {
		this.hidden = hidden;
		this.dispatchEvent(new Event('visibilitychange'));
	}
}
const turn = () => new Promise<void>((resolve) => setTimeout(resolve, 10));

test('visible poll serializes requests and ignores hidden tab wakeups', async () => {
	const visibility = new Visibility();
	let calls = 0;
	let finish!: () => void;
	const stop = startVisiblePoll(
		async () => {
			calls++;
			await new Promise<void>((r) => {
				finish = r;
			});
		},
		visibility,
		1
	);
	visibility.change(false);
	await turn();
	assert.equal(calls, 1);
	visibility.change(true);
	finish();
	await turn();
	assert.equal(calls, 1);
	visibility.change(false);
	assert.equal(calls, 2);
	stop();
	finish();
	await turn();
	assert.equal(calls, 2);
});

test('teardown aborts an in-flight request and removes the visibility listener', async () => {
	const visibility = new Visibility();
	let calls = 0;
	let signal!: AbortSignal;
	let finish!: () => void;
	const stop = startVisiblePoll(
		async (s) => {
			calls++;
			signal = s;
			await new Promise<void>((r) => {
				finish = r;
			});
		},
		visibility,
		1
	);
	stop();
	assert.equal(signal.aborted, true);
	finish();
	visibility.change(false);
	await turn();
	assert.equal(calls, 1);
});

test('a page mounted hidden waits for visibility before its first request', async () => {
	const visibility = new Visibility();
	visibility.hidden = true;
	let calls = 0;
	const stop = startVisiblePoll(
		async () => {
			calls++;
		},
		visibility,
		1
	);
	await turn();
	assert.equal(calls, 0);
	visibility.change(false);
	assert.equal(calls, 1);
	stop();
	await turn();
	assert.equal(calls, 1);
});
