import assert from 'node:assert/strict';
import test from 'node:test';

import {
	DIAL_WEDGE_RADIUS,
	dialDasharray,
	fuelRows,
	quotaWindowCountLabel,
	runnerBlocks,
	slotChip,
	stickyCountdown
} from './railGauge.ts';
import type { QuotaShell } from './quota.ts';
import type { RunnerProfile, RunnerSticky, WakeRequest } from './runners.ts';

const profiles: RunnerProfile[] = [
	{ name: 'codex', shell: 'codex', model: 'default', selected: true },
	{ name: 'codex-full', shell: 'codex', model: 'gpt-5.6-sol' }
];

const request: WakeRequest = {
	request_id: 'wake-1',
	profile: 'codex-full',
	repo_label: null,
	environment: null,
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

// #932 made visible (2026-08-08): the conversation-sticky is the standing
// truth for its bound thread, and the header must foreground it — until
// this landed the header showed the config pin while the sticky decided
// every wake in the thread.
const sticky: RunnerSticky = {
	profile: 'codex-full',
	claimed_at: '2026-07-18T12:00:00Z',
	expires_at: '2026-07-18T14:00:00Z',
	correspondent_key: 'telegram:user-id:1'
};
const duringSticky = Date.parse('2026-07-18T13:13:00Z');

test('a live sticky foregrounds the riding profile with a countdown badge', () => {
	assert.deepEqual(runnerBlocks(profiles, 'codex', null, sticky, duringSticky), [
		{
			profile: profiles[1],
			kind: 'sticky',
			badge: 'riding thread · 47m',
			active: true,
			until: '2026-07-18T14:00:00Z'
		},
		{ profile: profiles[0], kind: 'default', badge: 'default', active: false }
	]);
});

test('an expired or unmatched sticky falls back to the default block', () => {
	const afterExpiry = Date.parse('2026-07-18T14:00:01Z');
	assert.deepEqual(runnerBlocks(profiles, 'codex', null, sticky, afterExpiry), [
		{ profile: profiles[0], kind: 'default', badge: 'default', active: true }
	]);
	// A sticky naming a profile this rack doesn't carry cannot render.
	assert.deepEqual(
		runnerBlocks(profiles, 'codex', null, { ...sticky, profile: 'gone' }, duringSticky),
		[{ profile: profiles[0], kind: 'default', badge: 'default', active: true }]
	);
});

test('a parked request outranks the sticky in the header', () => {
	const blocks = runnerBlocks(profiles, 'codex', request, sticky, duringSticky);
	assert.equal(blocks[0]?.kind, 'requested');
});

test('stickyCountdown speaks the fuel dial reset grammar', () => {
	assert.equal(stickyCountdown(sticky, duringSticky), '47m');
	assert.equal(stickyCountdown(sticky, Date.parse('2026-07-18T12:10:00Z')), '1h50m');
	assert.equal(stickyCountdown({ ...sticky, expires_at: null }, duringSticky), null);
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

test('fuelRows derives countdown and remaining-window fraction from resets_at', () => {
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
	// The dial DRAINS: 2h30m left of a 5h window is half the wedge still
	// standing, not half of it spent. A test asserting 0.5 here would pass
	// under either reading, so the week row below carries the direction —
	// 4d2h left of 7d is most of the window remaining, and only the draining
	// reading puts it above a half.
	assert.ok(Math.abs((rows[0].timeRemaining ?? 0) - 0.5) < 0.001);
	assert.match(rows[0].tooltip, /50% of window left$/u);
	assert.ok(!rows[2].tooltip.includes('of window left'));
	assert.equal(rows[1].resetShort, '4d2h');
	assert.ok(Math.abs((rows[1].timeRemaining ?? 0) - (4 * 86400 + 2 * 3600) / (7 * 86400)) < 0.001);
	assert.ok((rows[1].timeRemaining ?? 0) > 0.5);
	assert.equal(rows[2].resetShort, null);
	assert.equal(rows[2].timeRemaining, null);
});

test('stale fuel rows render the last-known value with its scrape time', () => {
	const asOf = '2026-08-02T05:40:00Z';
	const rows = fuelRows([
		{
			shell: 'codex',
			status: 'stale',
			as_of: asOf,
			windows: [
				{
					label: 'weekly',
					used: null,
					limit: null,
					percent: null,
					reset: null,
					resets_at: null,
					last_known: {
						used: 58,
						limit: 100,
						percent: 42,
						reset: 'resets Sunday',
						resets_at: 1_786_320_000
					}
				}
			]
		}
	]);
	const expectedTime = new Date(asOf).toLocaleTimeString([], {
		hour: '2-digit',
		minute: '2-digit'
	});

	assert.equal(rows[0].percentLabel, `42% · as of ${expectedTime}`);
	assert.equal(rows[0].stale, true);
	assert.notEqual(rows[0].resetShort, null);
	assert.match(rows[0].tooltip, /42% left · resets Sunday/u);
});

// #1503 "the tank of dead quotas": `daemon_stale` (this shell's own daemon
// report freshness) is distinct from `status === 'stale'` (#237's
// scrape-level fact) — a `known` shell can still carry a stale report from a
// retired daemon, and the fuel row needs to dim for that reason too, with a
// title naming why.
test('a daemon-stale shell dims its fuel row and names the reason in the tooltip, even when `known`', () => {
	const rows = fuelRows([
		{
			shell: 'codex',
			status: 'known',
			daemon_stale: true,
			windows: [{ label: 'weekly', used: null, limit: null, percent: 87, reset: null }]
		}
	]);

	assert.equal(rows[0].stale, false, 'scrape-level status is unaffected — a distinct fact');
	assert.equal(rows[0].daemonStale, true);
	assert.match(rows[0].tooltip, /daemon report is outdated/u);
});

test('quota window count names the rows in the fuel grid, not their shells', () => {
	const shells: QuotaShell[] = [
		{ shell: 'claude', status: 'known', windows: [{ label: '5h' }, { label: 'weekly' }] },
		{ shell: 'codex', status: 'known', windows: [{ label: 'weekly' }, { label: 'monthly' }] }
	].map((shell) => ({
		...shell,
		windows: shell.windows.map((window) => ({
			...window,
			used: null,
			limit: null,
			percent: null,
			reset: null
		}))
	}));

	assert.equal(quotaWindowCountLabel(shells), '4 quota windows');
});

test('fuelRows clamps an already-passed reset to zero, empty dial', () => {
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
	// A window already past its reset has nothing left to run: the wedge is
	// empty, not full. Under the old filling reading this same case asserted 1.
	assert.equal(rows[0].timeRemaining, 0);
});

test('dialDasharray draws the remaining wedge proportionally and clamps', () => {
	const circumference = 2 * Math.PI * DIAL_WEDGE_RADIUS;
	assert.equal(dialDasharray(0), `0.000 ${circumference.toFixed(3)}`);
	assert.equal(dialDasharray(1), `${circumference.toFixed(3)} ${circumference.toFixed(3)}`);
	assert.equal(dialDasharray(0.5).split(' ')[0], (circumference / 2).toFixed(3));
	// Out-of-range fractions clamp instead of drawing an impossible arc.
	assert.equal(dialDasharray(1.7), dialDasharray(1));
	assert.equal(dialDasharray(-0.3), dialDasharray(0));
});

// ── the spawn-slot capacity chip (#972: LIMITS folds into the rail) ────────

test('slotChip stays neutral chrome below 80% utilization', () => {
	const chip = slotChip(1, 4);
	assert.equal(chip.label, '1/4 slots');
	assert.equal(chip.level, null);
	assert.match(chip.title, /spawn\.max_concurrent/);
});

test('slotChip speaks the quota vocabulary at contention', () => {
	// 4/5 in use → 80% utilization, 20% headroom → cooling; full → spent.
	assert.equal(slotChip(4, 5).level, 'cooling');
	assert.equal(slotChip(4, 4).level, 'spent');
	// 3/4 is 75% — still merely a configured ceiling, not contention.
	assert.equal(slotChip(3, 4).level, null);
});

test('slotChip renders an unpublished ceiling as a question, not a guess', () => {
	assert.equal(slotChip(2, null).label, '2/? slots');
	assert.equal(slotChip(2, null).level, null);
	assert.equal(slotChip(2, 0).label, '2/? slots');
});

// THE PICKER YOU CANNOT REACH (2026-08-02) and `railIsSlim`, its fix, are
// both gone (w-68, the gauge/bench split, 2026-08-19): the gauge has exactly
// one render now — one line, fixed height, sticky forever, no disclosure —
// so there is no longer a scroll verdict for a reader's own `open` to
// outrank. `git log` on this file carries the four tests this replaced, and
// `collapse.ts`'s own tests still cover `isCollapsed`/`tapVerdict` for the
// machine dock, which kept its full/docked distinction.
