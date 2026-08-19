// w-68 prep: the rail's own instrument. Boots the real vite dev server,
// mocks every /v1/dashboard/* route from repro/fixtures.mjs (same pattern
// drive.mjs/repro*.mjs already use), opens the rack, and reads
// `getBoundingClientRect()` for each of the rail's named sections at two
// widths and two catalog/data scales — replacing "look at a screenshot and
// say 977px" with a number a script produced.
//
// The seven content sections named in the task, plus the fold-bar control
// (rendered only while `condensed`, so it is 0px in every capture this
// script takes — see the report's own note on that): fold-bar · next-pick ·
// fuel · tank · error-note · project · environment · spool-rack. Each has a
// `data-measure="<name>"` attribute on its own container (added to
// ControlStrip.svelte / SpoolRack.svelte for exactly this purpose — see
// those files' git history for the pixel-parity proof that the attributes
// alone changed nothing).
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

// Render order, matching the task's own listing. `fold-bar` is included for
// completeness (it has a `data-measure` attribute like every other section)
// even though nothing in this script's own capture path renders it — see
// the report.
const SECTIONS = [
	'fold-bar',
	'next-pick',
	'fuel',
	'tank',
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

/** One measurement pass: heights of every named section, the rail's own
 * total, and whether the inner 100svh scroll container overflowed. */
async function measure(page) {
	return page.evaluate((sections) => {
		function rectHeight(el) {
			return el ? Math.round(el.getBoundingClientRect().height * 100) / 100 : null;
		}
		const out = { sections: {} };
		for (const name of sections) {
			out.sections[name] = rectHeight(document.querySelector(`[data-measure="${name}"]`));
		}
		// The rail container: THE STACK's own sticky/relative wrapper is
		// `.z-40` (repro2.mjs already keys off this same class for the same
		// element); its first child is the `max-h-[100svh] overflow-y-auto`
		// div ControlStrip renders inside — same convention repro2.mjs uses
		// for `rail`/`reserve`, so this script adds no new selector
		// convention of its own.
		const stack = document.querySelector('.z-40');
		const rail = stack ? stack.firstElementChild : null;
		out.railTotalHeight = rectHeight(rail);
		out.railScrollHeight = rail ? rail.scrollHeight : null;
		out.railClientHeight = rail ? rail.clientHeight : null;
		out.railOverflowed = rail ? rail.scrollHeight > rail.clientHeight + 1 : null;
		return out;
	}, SECTIONS);
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

			// Open the rack: project/environment/spool-rack (and error-note's
			// container) only mount while `expanded` — see ControlStrip.svelte's
			// `{#if expanded}` block. At scrollY 0 this never triggers `condensed`
			// (onRackChange's own scroll-to-top branch is a no-op here), so
			// `slim` stays false throughout and nothing about this click changes
			// which measurement regime we're in.
			await page.getByRole('button', { name: 'expand the rack' }).click();
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
				const firstRow = page.locator('[data-measure="spool-rack"] button').first();
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
	const header = ['scale', 'width', ...SECTIONS, 'rail total', 'overflowed?'];
	const rows = results.map((r) => [
		r.variant ? `${r.scale}+note` : r.scale,
		r.width,
		...SECTIONS.map((s) => (r.sections[s] === null ? '—' : String(r.sections[s]))),
		r.railTotalHeight === null ? '—' : String(r.railTotalHeight),
		r.railOverflowed === null ? '—' : r.railOverflowed ? 'YES' : 'no'
	]);
	const widths = header.map((h, i) => Math.max(h.length, ...rows.map((row) => row[i].length)));
	const fmt = (cols) => cols.map((c, i) => c.padEnd(widths[i])).join('  ');
	console.log(fmt(header));
	console.log(widths.map((w) => '-'.repeat(w)).join('  '));
	for (const row of rows) console.log(fmt(row));
	console.log(`\nwritten: ${OUT}/results-${SCALE_NAME}.json`);
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
