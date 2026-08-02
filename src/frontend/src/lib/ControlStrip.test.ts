import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ControlStrip.svelte');
const generated = join(here, '.controlStrip.generated.mjs');

// The rail's two faces (his 08-02 steer: "the resource management should
// stay on top, maybe in a collapsed way"): the full strip when the reader
// is at the top of the page, and a one-line slim bar once they scroll —
// the page's `condensed` verdict, overridable in the client by pinning the
// rail back open ($effect state, so a server render always shows the slim
// bar when condensed). Same server-side render dance as WarpStack's tests:
// compile with stubbed children, assert on the produced markup.
async function renderStrip(props: {
	runners: null;
	shells: null;
	condensed?: boolean;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'ControlStrip'
	});
	const runnable = compiled.js.code
		.replace(/import\s+SpoolRack\s+from\s*'\.\/SpoolRack\.svelte';/, 'const SpoolRack = () => {};')
		.replace(/'\.\/controlStrip'/g, "'./controlStrip.ts'")
		.replace(/'\.\/quota'/g, "'./quota.ts'")
		.replace(/'\.\/tankForecast'/g, "'./tankForecast.ts'")
		.replace(/'\.\/statusPalette'/g, "'./statusPalette.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('at rest the rail is the full strip, and the block is named "next pick" — never "dispatch"', async () => {
	const body = await renderStrip({ runners: null, shells: null });
	ok(body.includes('next pick'), 'the renamed block header renders');
	ok(!body.includes('dispatch'), 'the retired name is gone from the markup');
	ok(!body.includes('expand the rail'), 'no slim bar while uncondensed');
});

test('condensed, the rail is one slim line that expands on demand', async () => {
	const body = await renderStrip({ runners: null, shells: null, condensed: true });
	ok(body.includes('expand the rail'), 'the slim bar renders');
	ok(body.includes('rail'), 'named for what it is');
	ok(!body.includes('next pick'), 'the full strip stays folded');
});
