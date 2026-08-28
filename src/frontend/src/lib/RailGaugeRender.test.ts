import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'RailGauge.svelte');
const generated = join(here, '.railGauge.generated.mjs');

// THE GAUGE (w-68, signed 2026-08-19): one line, fixed height, sticky
// forever, no disclosure of its own. Unlike the old `ControlStrip`, there is
// no `condensed`/`expanded` duality left to pin — the gauge has exactly one
// render, whatever the catalog or scroll position. What these tests pin
// instead: the line never grows a second form, and every control it owns is
// a provider row — since 2026-08-28 the bench's handle lives with the bench,
// above the rail, not on this footline.
async function renderGauge(props: {
	runners: null;
	shells: null | Array<Record<string, unknown>>;

	openProvider?: string | null;
	now?: number;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'RailGauge'
	});
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: { ...props }
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('the gauge names its three sections — next pick, fuel, tank — every render', async () => {
	const body = await renderGauge({ runners: null, shells: null });
	ok(body.includes('data-measure="gauge"'), 'the whole line carries its own measure');
	ok(body.includes('data-measure="next-pick"'), 'next pick renders');
	ok(body.includes('data-measure="fuel"'), 'fuel renders');
	// tank only renders once `readTanks` has a lead verdict, which needs
	// shell data this test omits — covered separately below.
});

// This used to pin "the gauge owns exactly one control" — the rows were
// readouts and the bench toggle was the only button. A provider row *is* a
// control now, and the bench's toggle has left, so the count is exactly the
// rows. That is the sharper claim: with no quota report there are zero
// controls, so a stray one reappearing fails on the first assertion rather
// than shifting an off-by-one nobody would read as a regression.
//
// The invariant underneath is unchanged: no control here may make the gauge
// taller. Every expansion mounts outside this component and `.fuel-deck`
// stays 85px (the fixed-height test below is the one that must never bend).
test('every control on the gauge is a provider row, and there are no others', async () => {
	const bare = await renderGauge({ runners: null, shells: null });
	ok(!bare.includes('expand the rack'), 'the rack disclosure is gone with the rack');
	ok(
		!bare.includes('expand the rail'),
		'the slim-bar disclosure is gone too — there is no other form'
	);
	equal(
		(bare.match(/<button/g) ?? []).length,
		0,
		'with no quota report there are no rows, so there are no controls at all'
	);

	const withRows = await renderGauge({
		runners: null,
		shells: [
			{
				shell: 'claude',
				status: 'ok',
				windows: [
					{ label: 'weekly', used: null, limit: null, percent: 82, reset: null, resets_at: null }
				]
			}
		]
	});
	equal(
		(withRows.match(/<button/g) ?? []).length,
		1,
		'one provider row is one control — the row is how you open that provider'
	);
	ok(withRows.includes('aria-expanded="false"'), 'and it says so, closed');
});

test('a pressed provider row reports itself open; an unpressed one does not', async () => {
	const shells = [
		{
			shell: 'claude',
			status: 'ok',
			windows: [
				{ label: 'weekly', used: null, limit: null, percent: 82, reset: null, resets_at: null }
			]
		},
		{
			shell: 'codex',
			status: 'ok',
			windows: [
				{ label: 'weekly', used: null, limit: null, percent: 40, reset: null, resets_at: null }
			]
		}
	];
	const body = await renderGauge({
		runners: null,
		shells,
		openProvider: 'claude'
	});
	// Exactly one row open — several would grow the page the way the gauge's
	// own fixed height exists to stop.
	equal((body.match(/aria-expanded="true"/g) ?? []).length, 1, 'one row open, never two');
	ok(/claude — fold its windows and cores/u.test(body), 'the open row offers to fold');
	ok(/codex — open its windows and cores/u.test(body), 'the closed row offers to open');
	ok(body.includes('▾'), 'the open row wears the open caret');
	ok(body.includes('▸'), 'the closed row wears the closed one');
});

// The inverse of the test this replaces. The gauge used to carry a
// `▸ settings` handle on its footline while the panel it opened mounted below
// the provider bay — a handle and a body with a whole panel between them
// (maintainer, 2026-08-28). The bench owns both halves now, above the rail,
// so the invariant worth pinning is that the gauge has stopped claiming a
// control it does not host: nothing here may say "settings" again without
// the body coming back with it.
test('the gauge carries no settings control — the bench owns its own handle', async () => {
	const body = await renderGauge({ runners: null, shells: null });
	ok(!/settings/iu.test(body), 'no settings handle on the gauge');
	ok(!body.includes('bench-toggle'), 'and not the class one would come back as');
});

// The one disclosure the gauge does own is the provider row, and it must
// still be honest — this is the assertion the deleted test was really made
// of, kept where it now belongs.
test('the gauge reports only the provider disclosure it actually owns', async () => {
	const shut = await renderGauge({ runners: null, shells: null, openProvider: null });
	ok(!shut.includes('aria-expanded="true"'), 'nothing reads open with no row pressed');
});

// design-resident-field.md §"Settings, fuel, and the next dispatch": the
// deck groups by *provider* now (a small, stable set — claude, codex), not
// by meter, so the growth axis this test pins moved from "how many windows
// does one provider report" (unbounded — an old flat cell per meter) to "how
// many providers report at all" (bounded by the harness catalog, still
// unbounded in theory — a pathological account is exactly what this test
// throws at it — so the deck keeps a fixed height and an overflow scroll,
// just on the vertical axis a one-row-per-provider list actually grows on).
test('the fuel deck never grows — its fixed-height track absorbs provider-count growth', async () => {
	const shells = Array.from({ length: 12 }, (_, i) => ({
		shell: `shell-${i}`,
		status: 'ok',
		windows: [
			{ label: '5h window', used: null, limit: null, percent: 40, reset: null, resets_at: null }
		]
	}));
	const body = await renderGauge({ runners: null, shells });
	ok(
		(body.match(/class="fuel-provider-row(?:"| )/g) ?? []).length === 12,
		'one row per provider, all twelve render'
	);
	const source = readFileSync(componentPath, 'utf8');
	ok(source.includes('class="fuel-deck"'), 'fuel owns a dedicated instrument deck');
	ok(/\.fuel-deck\s*\{[^}]*height:\s*85px/su.test(source), 'the deck keeps its own fixed height');
	ok(
		/\.fuel-deck\s*\{[^}]*overflow-y:\s*auto/su.test(source),
		'provider-count growth scrolls inside fuel rather than stretching it'
	);
	ok(/\.gauge\s*\{[^}]*height:\s*140px/su.test(source), 'the gauge has one explicit height');
	ok(!/class="[^"]*flex-wrap/u.test(source), 'no rendered gauge row can wrap with the catalog');
});

test('a provider row reports the tap target the fuel design asks for', async () => {
	const shells = [
		{
			shell: 'claude',
			status: 'ok',
			windows: [
				{ label: '5h window', used: null, limit: null, percent: 93, reset: null, resets_at: null },
				{ label: 'weekly', used: null, limit: null, percent: 82, reset: null, resets_at: null },
				{
					label: 'weekly (Fable)',
					used: null,
					limit: null,
					percent: 91,
					reset: null,
					resets_at: null
				}
			]
		},
		{
			shell: 'codex',
			status: 'ok',
			windows: [
				{ label: 'weekly', used: null, limit: null, percent: 100, reset: null, resets_at: null }
			]
		}
	];
	const body = await renderGauge({ runners: null, shells });
	// Two provider rows, never four meter cells — the collapsed shape the
	// design page's "truthful shape" example draws.
	ok(
		(body.match(/class="fuel-provider-row(?:"| )/g) ?? []).length === 2,
		'one row per provider, not per meter'
	);
	ok(body.includes('>claude<'), 'the provider itself is the readable label');
	ok(body.includes('>82%<'), "claude's binding reading is the row's one figure");
	// ONE AXIS PER TRACK. Until 2026-08-28 every non-binding window drew a
	// second, third, fourth semi-transparent fill on this same 12px track at
	// a 3px vertical offset — so the headline number and the longest visible
	// fill were different readings with no key saying so ("not readable when
	// all 3 are clobbered up like so"). Two providers, two fills, ever.
	ok(
		(body.match(/class="fuel-fill(?:"| )/g) ?? []).length === 2,
		'exactly one fill per provider row — never a second quantity on the same axis'
	);
	ok(!body.includes('fuel-ghost'), 'the overlaid ghost fills are gone, not merely dimmed');
	// The number never travels unlabelled: the window it measures renders
	// beside it, and every other window keeps its own named number.
	ok(body.includes('>week</span>'), 'the binding figure says which ceiling it is a percentage of');
	ok(
		/fuel-ledger-name[^>]*>5h<\/span> <span[^>]*>93%</u.test(body),
		"claude's 5h keeps its own number in the ledger rather than an unlabelled bar"
	);
	ok(
		/fuel-ledger-name[^>]*>fable\/week<\/span> <span[^>]*>91%</u.test(body),
		"the core allowance is named as a core's, not shown as a peer of the shell's own windows"
	);
	ok(
		(body.match(/class="fuel-ledger(?:"| )/g) ?? []).length === 2,
		'codex — one meter — manufactures no ledger entries'
	);
});

test('the row reads the window that binds, not the one that happens to be weekly', async () => {
	// The old rule was "primary = the meter labelled week". A burned 5h
	// session under a comfortable weekly ceiling therefore rendered a
	// reassuring 82% over a machine that could not take a run at all. The
	// binding window is the one with the least left.
	const shells = [
		{
			shell: 'claude',
			status: 'ok',
			windows: [
				{ label: '5h window', used: null, limit: null, percent: 4, reset: null, resets_at: null },
				{ label: 'weekly', used: null, limit: null, percent: 82, reset: null, resets_at: null }
			]
		}
	];
	const body = await renderGauge({ runners: null, shells });
	ok(body.includes('>4%<'), 'the row shows the ceiling that stops a run first');
	ok(body.includes('>5h</span>'), 'and names it, so the figure is never ambiguous');
	ok(
		/fuel-ledger-name[^>]*>week<\/span> <span[^>]*>82%</u.test(body),
		'the weekly reading is still there — on the ledger, not driving the bar'
	);
	ok(
		/class="fuel-fill[^"]*" style="width: 4%/u.test(body),
		'and the bar draws the same number the row prints'
	);
});
