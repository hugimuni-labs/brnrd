import assert from 'node:assert/strict';
import test from 'node:test';

import { fuelRows, runnerBlocks } from './controlStrip.ts';
import type { QuotaShell } from './quota.ts';
import type { RunnerProfile, WakeRequest } from './runners.ts';

const profiles: RunnerProfile[] = [
	{ name: 'codex', shell: 'codex', model: 'default', selected: true },
	{ name: 'codex-full', shell: 'codex', model: 'gpt-5.6-sol' }
];

const request: WakeRequest = {
	request_id: 'wake-1',
	profile: 'codex-full',
	requested_at: '2026-07-18T12:00:00Z',
	status: 'pending'
};

test('default-only runner summary is one active block with an honest badge', () => {
	assert.deepEqual(runnerBlocks(profiles, 'codex', null), [
		{ profile: profiles[0], kind: 'default', badge: 'default', active: true }
	]);
});

test('a distinct request foregrounds intent while retaining the ghosted default', () => {
	assert.deepEqual(runnerBlocks(profiles, 'codex', request), [
		{ profile: profiles[1], kind: 'requested', badge: 'requested · next wake', active: true },
		{ profile: profiles[0], kind: 'default', badge: 'default', active: false }
	]);
});

test('a request matching the default never renders duplicate runner blocks', () => {
	assert.deepEqual(runnerBlocks(profiles, 'codex', { ...request, profile: 'codex' }), [
		{ profile: profiles[0], kind: 'requested', badge: 'requested · next wake', active: true }
	]);
});

test('selected profile backstops a report without an explicit default name', () => {
	assert.equal(runnerBlocks(profiles, null, null)[0]?.profile.name, 'codex');
	assert.deepEqual(runnerBlocks([], null, null), []);
});

test('fuel rows derive compact shell and model labels from every reported window', () => {
	const shells: QuotaShell[] = [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{
					label: '5h window',
					used: null,
					limit: null,
					percent: 61.2,
					reset: 'resets 17:00',
					resets_at: null
				},
				{
					label: 'weekly',
					used: null,
					limit: null,
					percent: 48,
					reset: null,
					resets_at: null
				},
				{
					label: 'weekly (Fable)',
					used: null,
					limit: null,
					percent: 25,
					reset: 'resets Jul 19',
					resets_at: null
				}
			]
		},
		{
			shell: 'codex',
			status: 'stale',
			windows: [
				{
					label: 'weekly',
					used: null,
					limit: null,
					percent: null,
					reset: null,
					resets_at: 1_784_490_643
				}
			]
		}
	];

	const rows = fuelRows(shells);
	assert.deepEqual(
		rows.map(({ label, percentLabel, stale }) => ({ label, percentLabel, stale })),
		[
			{ label: 'claude · 5h', percentLabel: '61%', stale: false },
			{ label: 'claude · week', percentLabel: '48%', stale: false },
			{ label: 'fable · week', percentLabel: '25%', stale: false },
			{ label: 'codex · week', percentLabel: '?', stale: true }
		]
	);
	assert.equal(rows[0].tooltip, 'claude · 5h: 61% left · resets 17:00');
	assert.match(rows[3].tooltip, /unknown · resets 2026-/u);
});

test('fuelRows derives countdown and window-elapsed fraction from resets_at', () => {
	const nowMs = 1_784_400_000_000; // epoch seconds 1_784_400_000
	const shells = [
		{
			shell: 'claude',
			status: 'ok',
			windows: [
				{
					label: '5h window',
					used: null,
					limit: null,
					percent: 61,
					reset: 'resets 17:00',
					resets_at: 1_784_400_000 + 2 * 3600 + 30 * 60 // 2h30m left of 5h
				},
				{
					label: 'weekly',
					used: null,
					limit: null,
					percent: 48,
					reset: null,
					resets_at: 1_784_400_000 + 4 * 86400 + 2 * 3600 // 4d2h left of 7d
				},
				{
					label: 'weekly',
					used: null,
					limit: null,
					percent: 10,
					reset: null
					// no resets_at: older daemon report
				}
			]
		}
	];

	const rows = fuelRows(shells, nowMs);
	assert.equal(rows[0].resetShort, '2h30m');
	assert.ok(Math.abs((rows[0].timeFraction ?? 0) - 0.5) < 0.001);
	assert.equal(rows[1].resetShort, '4d2h');
	assert.ok(
		Math.abs((rows[1].timeFraction ?? 0) - (1 - (4 * 86400 + 2 * 3600) / (7 * 86400))) < 0.001
	);
	assert.equal(rows[2].resetShort, null);
	assert.equal(rows[2].timeFraction, null);
});

test('fuelRows clamps an already-passed reset to zero, full window', () => {
	const nowMs = 1_784_400_000_000;
	const shells = [
		{
			shell: 'claude',
			status: 'ok',
			windows: [
				{
					label: '5h window',
					used: null,
					limit: null,
					percent: 0,
					reset: null,
					resets_at: 1_784_400_000 - 60
				}
			]
		}
	];

	const rows = fuelRows(shells, nowMs);
	assert.equal(rows[0].resetShort, '0m');
	assert.equal(rows[0].timeFraction, 1);
});
