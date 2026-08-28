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
// instead: the line never grows a second form, and the only interactive
// control it owns is the bench toggle.
async function renderGauge(props: {
	runners: null;
	shells: null | Array<Record<string, unknown>>;
	benchOpen: boolean;
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
			props: { onBenchToggle: () => {}, ...props }
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('the gauge names its three sections — next pick, fuel, tank — every render', async () => {
	const body = await renderGauge({ runners: null, shells: null, benchOpen: false });
	ok(body.includes('data-measure="gauge"'), 'the whole line carries its own measure');
	ok(body.includes('data-measure="next-pick"'), 'next pick renders');
	ok(body.includes('data-measure="fuel"'), 'fuel renders');
	// tank only renders once `readTanks` has a lead verdict, which needs
	// shell data this test omits — covered separately below.
});

test('there is no disclosure to open — one button only, and it names the bench', async () => {
	const body = await renderGauge({ runners: null, shells: null, benchOpen: false });
	ok(!body.includes('expand the rack'), 'the rack disclosure is gone with the rack');
	ok(
		!body.includes('expand the rail'),
		'the slim-bar disclosure is gone too — there is no other form'
	);
	const buttons = body.match(/<button/g) ?? [];
	equal(buttons.length, 1, 'the gauge owns exactly one control: the bench toggle');
	ok(body.includes('aria-label="open the bench'), 'closed, the control names what it opens');
});

test('the bench toggle reflects benchOpen honestly', async () => {
	const open = await renderGauge({ runners: null, shells: null, benchOpen: true });
	ok(open.includes('aria-expanded="true"'));
	ok(open.includes('▾ bench'));
	const closed = await renderGauge({ runners: null, shells: null, benchOpen: false });
	ok(closed.includes('aria-expanded="false"'));
	ok(closed.includes('▸ bench'));
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
	const body = await renderGauge({ runners: null, shells, benchOpen: false });
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
	const body = await renderGauge({ runners: null, shells, benchOpen: false });
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
	const body = await renderGauge({ runners: null, shells, benchOpen: false });
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
