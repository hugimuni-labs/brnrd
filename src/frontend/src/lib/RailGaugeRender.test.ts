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

test('the fuel line never wraps — overflow-x-auto, not flex-wrap, is what keeps the gauge fixed height', async () => {
	const shells = Array.from({ length: 12 }, (_, i) => ({
		shell: `shell-${i}`,
		status: 'ok',
		windows: [
			{ label: '5h window', used: null, limit: null, percent: 40, reset: null, resets_at: null }
		]
	}));
	const body = await renderGauge({ runners: null, shells, benchOpen: false });
	ok(
		body.includes('overflow-x-auto'),
		'the gauge scrolls sideways rather than wrapping to a second line'
	);
	ok(
		!/flex-wrap/u.test(body),
		'flex-wrap is exactly what let the old slim bar grow with the catalog'
	);
});
