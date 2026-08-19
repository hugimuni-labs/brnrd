// w-68's own instrument, and the acceptance check for the gauge/bench split
// it built. Boots the real vite dev server, mocks every /v1/dashboard/*
// route from repro/fixtures.mjs (same pattern drive.mjs/repro*.mjs already
// use), opens the bench, and reads `getBoundingClientRect()` for each named
// section at two widths and two catalog/data scales — replacing "look at a
// screenshot and say 977px" with a number a script produced.
//
// Section names updated for the split (2026-08-19): `fold-bar` is gone with
// the condense/pin duality it belonged to (the gauge has exactly one form
// now). `gauge` and `bench` are new — the two top-level surfaces the split
// created — wrapping the same three/five sub-sections the original script
// named: gauge → next-pick · fuel · tank; bench → error-note · project ·
// environment · spool-rack. Each still carries its own
// `data-measure="<name>"` attribute (`RailGauge.svelte` / `RailBench.svelte`
// / `SpoolRack.svelte`).
//
// Usage:
//   node repro/measure-rail.mjs [--stress] [--out DIR] [--port N] [--note]
//
//   --stress   use fixtures.STRESS_SCALE instead of fixtures.DEFAULT_SCALE
//              (many repos/environments/shells/cores/quota-windows — the
//              pathological account the historical 977px/1502px figures
//              were hand-measured against)
//   --out DIR  output directory for results.json + screenshots
//              (default /tmp/rail-measure)
//   --port N   vite dev port (default 5186 — the other repro scripts use
//              5183-5185, so this stays out of their way if run alongside)
//   --note     after the baseline capture, tap the pinned runner row (a
//              no-network "already the default" tap) so the error/note
//              receipts section renders real content instead of reading
//              0px in every capture — done once per width, after the
//              baseline measurement, and reported as a separate row

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir, writeFile } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const STRESS = args.includes('--stress');
const WITH_NOTE = args.includes('--note');
const OUT = argValue('--out') ?? '/tmp/rail-measure';
const PORT = Number(argValue('--port') ?? 5186);

function argValue(flag) {
	const i = args.indexOf(flag);
	if (i === -1 || i + 1 >= args.length) return null;
	return args[i + 1];
}

const SCALE_NAME = STRESS ? 'stress' : 'default';
const SCALE = STRESS ? fixtures.STRESS_SCALE : fixtures.DEFAULT_SCALE;
const ROUTES = fixtures.buildRoutes(SCALE);

// The two widths the task asks for. Desktop at 1200 (the task's floor);
// phone at the same 390x844 the existing repro scripts already standardize
// on for "a phone".
const WIDTHS = [
	{ name: 'phone', width: 390, height: 844 },
	{ name: 'desktop', width: 1200, height: 950 }
];

// Render order: the gauge's own three sub-sections, then the bench's four.
const SECTIONS = [
	'gauge',
	'next-pick',
	'fuel',
	'tank',
	'bench',
	'error-note',
	'project',
	'environment',
	'spool-rack'
];

async function waitForServer(url, tries = 60) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok || res.status === 404) return;
		} catch {
			/* not up yet */
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

/** One measurement pass: heights of every named section, plus the gauge's
 * own wrapper height — the acceptance number this script exists to produce
 * post-split: "the gauge's height must be identical at default and
 * `--stress` scale, at both widths." */
async function measure(page) {
	return page.evaluate((sections) => {
		function rectHeight(el) {
			return el ? Math.round(el.getBoundingClientRect().height * 100) / 100 : null;
		}
		function rect(el) {
			if (!el) return null;
			const r = el.getBoundingClientRect();
			return {
				left: Math.round(r.left * 100) / 100,
				right: Math.round(r.right * 100) / 100,
				width: Math.round(r.width * 100) / 100
			};
		}
		const out = { sections: {} };
		for (const name of sections) {
			out.sections[name] = rectHeight(document.querySelector(`[data-measure="${name}"]`));
		}
		// THE STACK's own sticky wrapper is `.z-40` (repro2.mjs already keys
		// off this same class for the same element); after the gauge/bench
		// split (w-68) its first child is the gauge's own `-mx-6 ...` wrapper
		// div — the bench no longer lives inside this container at all, so
		// this number is now specifically "how tall is the sticky gauge",
		// not "how tall is the whole expanded rail" (that question moved to
		// `out.sections.bench`, which has no fixed-height claim to check).
		const stack = document.querySelector('.z-40');
		const gaugeWrapper = stack ? stack.firstElementChild : null;
		out.gaugeWrapperHeight = rectHeight(gaugeWrapper);
		// Defect 1's own acceptance numbers (fixed 2026-08-19): fuel and tank
		// must land fully inside the viewport at `scrollLeft === 0` — the
		// meters a reader glances at without ever meaning to touch anything,
		// per the maintainer's report the fix was filed for.
		out.viewportWidth = window.innerWidth;
		out.scrollLeft = document.scrollingElement ? document.scrollingElement.scrollLeft : 0;
		out.fuelRect = rect(document.querySelector('[data-measure="fuel"]'));
		out.tankRect = rect(document.querySelector('[data-measure="tank"]'));
		return out;
	}, SECTIONS);
}

/** Defect 1's acceptance check: fuel and tank fully on-screen at
 * `scrollLeft === 0`. Returns a list of failure strings (empty ⇒ pass). */
function checkMetersVisible(entry) {
	const failures = [];
	if (entry.scrollLeft !== 0) {
		failures.push(`scrollLeft is ${entry.scrollLeft}, expected 0`);
	}
	for (const name of ['fuelRect', 'tankRect']) {
		const rect = entry[name];
		if (!rect) continue; // no lead tank reading is legal (no `{#if lead}`)
		if (rect.left < 0 || rect.right > entry.viewportWidth) {
			failures.push(
				`${name} spans [${rect.left}, ${rect.right}], outside [0, ${entry.viewportWidth}]`
			);
		}
	}
	return failures;
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	let viteLog = '';
	vite.stdout.on('data', (d) => (viteLog += d));
	vite.stderr.on('data', (d) => (viteLog += d));

	const results = [];

	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();

		for (const viewport of WIDTHS) {
			const context = await browser.newContext({
				viewport: { width: viewport.width, height: viewport.height }
			});
			const page = await context.newPage();
			await page.route('**/v1/dashboard/**', async (route) => {
				const url = new URL(route.request().url());
				const body = ROUTES[url.pathname];
				if (body) {
					await route.fulfill({
						status: 200,
						contentType: 'application/json',
						body: JSON.stringify(body)
					});
				} else {
					await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
				}
			});

			await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
			await page.waitForSelector('[data-measure="fuel"]', { timeout: 15000 });
			await delay(300); // let ignite transitions settle

			// Open the bench: project/environment/spool-rack (and error-note's
			// container) only mount while `benchOpen` — see `RailBench.svelte`'s
			// `{#if benchOpen}` in +page.svelte. At scrollY 0 this never touches
			// the gauge's own render (it has exactly one form now), so nothing
			// about this click changes what `out.gaugeWrapperHeight` measures.
			await page.getByRole('button', { name: /open the bench/ }).click();
			await page.waitForSelector('[data-measure="spool-rack"]', { timeout: 15000 });
			await delay(300); // glitchReveal's own transition window

			const baseline = await measure(page);
			const screenshotPath = `${OUT}/${SCALE_NAME}-${viewport.name}.png`;
			await page.screenshot({ path: screenshotPath, fullPage: false });

			const entry = {
				scale: SCALE_NAME,
				scaleParams: SCALE,
				width: viewport.name,
				viewport: { width: viewport.width, height: viewport.height },
				screenshot: screenshotPath,
				...baseline
			};
			results.push(entry);

			if (WITH_NOTE) {
				// Tap the pinned (default) runner row: `tapWakeRunner`'s
				// no-parked/is-default branch sets `runnersNote` with no network
				// call (src/routes/+page.svelte ~L207), so this needs no extra
				// route mock. It's the cheapest way to prove error-note isn't
				// permanently a 0px section — just usually empty on a fresh load.
				// `data-role="rack-row-tap"` (not a bare `button`) since the rack
				// is a two-stage picker now — the first plain `<button>` inside
				// `[data-measure="spool-rack"]` is a shell tab, not a runner row.
				const firstRow = page
					.locator('[data-measure="spool-rack"] button[data-role="rack-row-tap"]')
					.first();
				if (await firstRow.count()) {
					await firstRow.click();
					await delay(150);
					const withNote = await measure(page);
					const noteScreenshot = `${OUT}/${SCALE_NAME}-${viewport.name}-with-note.png`;
					await page.screenshot({ path: noteScreenshot, fullPage: false });
					results.push({
						scale: SCALE_NAME,
						scaleParams: SCALE,
						width: viewport.name,
						viewport: { width: viewport.width, height: viewport.height },
						screenshot: noteScreenshot,
						variant: 'note-triggered',
						...withNote
					});
				}
			}

			await context.close();
		}

		await browser.close();
	} finally {
		vite.kill();
		await writeFile(`${OUT}/vite.log`, viteLog);
	}

	await writeFile(`${OUT}/results-${SCALE_NAME}.json`, JSON.stringify(results, null, 2));

	// Human table.
	const header = ['scale', 'width', ...SECTIONS, 'gauge wrapper'];
	const rows = results.map((r) => [
		r.variant ? `${r.scale}+note` : r.scale,
		r.width,
		...SECTIONS.map((s) => (r.sections[s] === null ? '—' : String(r.sections[s]))),
		r.gaugeWrapperHeight === null ? '—' : String(r.gaugeWrapperHeight)
	]);
	const widths = header.map((h, i) => Math.max(h.length, ...rows.map((row) => row[i].length)));
	const fmt = (cols) => cols.map((c, i) => c.padEnd(widths[i])).join('  ');
	console.log(fmt(header));
	console.log(widths.map((w) => '-'.repeat(w)).join('  '));
	for (const row of rows) console.log(fmt(row));
	console.log(`\nwritten: ${OUT}/results-${SCALE_NAME}.json`);

	// --- Defect 1's acceptance check --------------------------------------
	// "extend measure-rail.mjs (or add a sibling check) to assert fuel's and
	// tank's getBoundingClientRect() land fully inside the 390px viewport
	// with scrollLeft === 0 — at default AND stress scale." Every captured
	// entry (baseline and, with `--note`, the tapped variant) is checked —
	// the bug this guards was specifically about the meters being pushed
	// off-screen by next-pick's own unbounded text, and that text renders in
	// every one of these variants.
	let failed = false;
	for (const entry of results) {
		const label = `${entry.variant ? `${entry.scale}+note` : entry.scale}/${entry.width}`;
		const failures = checkMetersVisible(entry);
		if (failures.length) {
			failed = true;
			console.error(`✗ ${label}: ${failures.join('; ')}`);
		} else {
			console.log(`✓ ${label}: fuel+tank fully on-screen at scrollLeft 0`);
		}
	}

	// The gauge's own fixed-height claim, restated as a same-run check: the
	// two widths captured in *this* invocation must read the identical
	// wrapper height — the number the table above already prints, just
	// compared rather than eyeballed. Cross-scale (default vs --stress)
	// still needs eyeballing across two separate invocations' JSON, since
	// each run only ever measures one scale.
	const baselineEntries = results.filter((r) => !r.variant);
	const heights = new Set(baselineEntries.map((r) => r.gaugeWrapperHeight));
	if (heights.size > 1) {
		failed = true;
		console.error(
			`✗ gauge wrapper height is not constant across widths at ${SCALE_NAME} scale: ${[...heights].join(', ')}`
		);
	} else {
		console.log(
			`✓ gauge wrapper height constant across widths at ${SCALE_NAME} scale: ${[...heights][0]}px`
		);
	}

	if (failed) {
		console.error('\nmeasure-rail: acceptance check failed');
		process.exitCode = 1;
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
