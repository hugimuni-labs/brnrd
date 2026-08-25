import assert from 'node:assert/strict';
import test from 'node:test';

import { fuelProviderGroups } from './fuelProviders.ts';
import type { QuotaShell } from './quota.ts';

test('both providers present: each gets its own group, claude carries the ghosts', () => {
	const shells: QuotaShell[] = [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{ label: '5h window', used: null, limit: null, percent: 93, reset: null },
				{ label: 'weekly', used: null, limit: null, percent: 82, reset: null },
				{ label: 'weekly (Fable)', used: null, limit: null, percent: 91, reset: null }
			]
		},
		{
			shell: 'codex',
			status: 'known',
			windows: [{ label: 'weekly', used: null, limit: null, percent: 100, reset: null }]
		}
	];

	const groups = fuelProviderGroups(shells);
	assert.equal(groups.length, 2);

	const claude = groups[0];
	assert.equal(claude.provider, 'claude');
	assert.equal(claude.primary?.label, 'claude · week');
	assert.equal(claude.primary?.percent, 82);
	assert.deepEqual(
		claude.secondary.map((meter) => meter.label),
		['claude · 5h', 'fable · week']
	);
	assert.equal(claude.meters.length, 3, 'every observed meter survives into the flat list');

	const codex = groups[1];
	assert.equal(codex.provider, 'codex');
	assert.equal(codex.primary?.label, 'codex · week');
	assert.equal(codex.primary?.percent, 100);
	assert.deepEqual(codex.secondary, [], 'a provider with one meter never manufactures a ghost');
});

test('a provider with one meter renders a primary and no ghosts', () => {
	const shells: QuotaShell[] = [
		{
			shell: 'codex',
			status: 'known',
			windows: [{ label: 'weekly', used: null, limit: null, percent: 40, reset: null }]
		}
	];
	const [codex] = fuelProviderGroups(shells);
	assert.equal(codex.primary?.percent, 40);
	assert.equal(codex.secondary.length, 0);
});

test('fable is a core allowance attached under claude, never a peer provider', () => {
	const shells: QuotaShell[] = [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{ label: 'weekly', used: null, limit: null, percent: 82, reset: null },
				{ label: 'weekly (Fable)', used: null, limit: null, percent: 25, reset: null }
			]
		}
	];
	const groups = fuelProviderGroups(shells);
	assert.equal(groups.length, 1, 'fable never becomes its own provider group');
	const fableMeter = groups[0].secondary.find((meter) => meter.coreId === 'fable');
	assert.ok(fableMeter, 'fable renders inside the claude group');
	assert.equal(fableMeter?.scope, 'core');
	assert.equal(fableMeter?.label, 'fable · week');
});

test('a missing meter renders no fake track: absent windows produce nothing, not a zero bar', () => {
	const shells: QuotaShell[] = [{ shell: 'codex', status: 'known', windows: [] }];
	const [codex] = fuelProviderGroups(shells);
	assert.equal(codex.primary, null);
	assert.deepEqual(codex.secondary, []);
	assert.deepEqual(codex.meters, []);
});

test('a provider reporting only a core-scoped allowance still surfaces it with no fabricated primary', () => {
	const shells: QuotaShell[] = [
		{
			shell: 'claude',
			status: 'known',
			windows: [{ label: 'weekly (Fable)', used: null, limit: null, percent: 60, reset: null }]
		}
	];
	const [claude] = fuelProviderGroups(shells);
	assert.equal(claude.primary, null, 'no provider-scope reading exists to promote');
	assert.equal(claude.meters.length, 1);
	assert.equal(claude.meters[0].scope, 'core');
});

test('stale readings carry their staleness flags through the grouping unchanged', () => {
	const shells: QuotaShell[] = [
		{
			shell: 'codex',
			status: 'stale',
			as_of: '2026-08-02T05:40:00Z',
			windows: [
				{
					label: 'weekly',
					used: null,
					limit: null,
					percent: null,
					reset: null,
					last_known: { used: 58, limit: 100, percent: 42, reset: 'resets Sunday', resets_at: null }
				}
			]
		},
		{
			shell: 'claude',
			status: 'known',
			daemon_stale: true,
			windows: [{ label: 'weekly', used: null, limit: null, percent: 87, reset: null }]
		}
	];
	const [codex, claude] = fuelProviderGroups(shells);
	assert.equal(codex.primary?.stale, true);
	assert.equal(codex.primary?.percentLabel.startsWith('42%'), true);
	assert.equal(claude.primary?.daemonStale, true);
	assert.equal(claude.primary?.stale, false, 'daemon-stale and scrape-stale stay distinct facts');
});
