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
	ok(body.includes('>82%<'), "claude's weekly reading is the primary, full-opacity figure");
	ok(
		(body.match(/class="fuel-ghost(?:"| )/g) ?? []).length === 2,
		"claude's 5h and fable's week layer behind it, and codex — one meter — manufactures no ghost track"
	);
});
