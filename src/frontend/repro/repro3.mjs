// Third-pass repro (the 2026-08-13 report): "when we scroll just at a topic
// split (e.g. between cloth and warp, or worksurface and cloth) it starts
// glitching between collapsed and normal shape of the rail frantically."
//
// Mechanism under test: the stack-wiring `$effect` in `+page.svelte` calls
// `step()` synchronously at setup, which makes `activeSection` (read at the
// scroll-spy compare) and `railOpen` tracked dependencies of the whole
// effect — against the effect's own comment. Every heading crossing then
// tears the machinery down and rebuilds it with `initialStackClocks()`,
// un-collapsing everything for one settle window; the geometry change moves
// the heading back across the boundary, and the loop self-sustains.
//
// So: scroll down past a section heading, drift slowly back up across it
// (the report's own gesture), land with the heading a few px below the
// collapsed stack's bottom edge, then hold still for 3 s and count
// rail-form transitions (condensed `pb-0` <-> full `pb-2`) plus
// docked-heddle mount/unmount per frame. A correct stack holds ZERO
// transitions while the reader holds still; the bug reads as dozens
// (36 rail flips + 36 heddle flips per 3 s hold, measured 2026-08-13).

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5186;
const OUT = process.env.REPRO_OUT ?? '/tmp/sticky-repro3';

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

async function main() {
	await import('node:fs/promises').then((fs) => fs.mkdir(OUT, { recursive: true }));
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		// Desktop this time — the report's own screenshot is a ~1090px window.
		const context = await browser.newContext({ viewport: { width: 1180, height: 860 } });
		const page = await context.newPage();
		await page.route('**/v1/dashboard/**', async (route) => {
			const url = new URL(route.request().url());
			const body = fixtures.ROUTES[url.pathname];
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
		await page.waitForSelector('text=Item number 1', { timeout: 15000 });
		await delay(600);

		const verdicts = [];
		for (const headingId of ['cloth-heading', 'corpus-heading']) {
			const result = await page.evaluate(async (id) => {
				const heading = document.getElementById(id);
				const stack = document.querySelector('.z-40');
				if (!heading || !stack) return { error: `missing ${id} or stack` };

				// Walk the viewport past the boundary: put the heading's top a
				// little ABOVE the stack's live bottom edge (section active,
				// stack settled/collapsed), re-measuring between moves since the
				// stack's own form changes both quantities.
				for (let i = 0; i < 6; i++) {
					const delta = heading.getBoundingClientRect().top - stack.getBoundingClientRect().bottom;
					if (Math.abs(delta + 10) <= 1) break;
					window.scrollTo(0, Math.max(0, window.scrollY + delta + 10));
					await new Promise((resolve) => setTimeout(resolve, 350));
				}

				// Now scroll slowly back UP across the boundary — the report's
				// own gesture ("we scroll just at a topic split") — landing with
				// the heading a few px BELOW the collapsed stack's bottom edge.
				// That position is inside the contested window: collapsed, the
				// heading reads "below" (previous section); any transient
				// expansion reads it "above" (this section). One upward crossing
				// arms it; a stable stack immediately re-settles and stays put.
				const from = window.scrollY;
				for (let i = 1; i <= 18; i++) {
					window.scrollTo(0, Math.max(0, from - i));
					await new Promise((resolve) => requestAnimationFrame(resolve));
				}

				// Hold still 3 s; sample every animation frame.
				const frames = [];
				let sampling = true;
				function sample() {
					const rail = stack.firstElementChild;
					const heddles = document.querySelector('[aria-label="the heddles · lens"]');
					frames.push({
						t: performance.now(),
						scrollY: window.scrollY,
						condensed: rail ? rail.className.includes('pb-0') : null,
						heddlesMounted: heddles !== null,
						stackHeight: stack.getBoundingClientRect().height,
						label: document.querySelector('.machine-dock a')?.textContent?.trim() ?? null
					});
					if (sampling) requestAnimationFrame(sample);
				}
				requestAnimationFrame(sample);
				await new Promise((resolve) => setTimeout(resolve, 3000));
				sampling = false;
				await new Promise((resolve) => requestAnimationFrame(resolve));

				let railFlips = 0;
				let heddleFlips = 0;
				let heightMoves = 0;
				for (let i = 1; i < frames.length; i++) {
					if (frames[i].condensed !== frames[i - 1].condensed) railFlips++;
					if (frames[i].heddlesMounted !== frames[i - 1].heddlesMounted) heddleFlips++;
					if (Math.abs(frames[i].stackHeight - frames[i - 1].stackHeight) > 0.5) heightMoves++;
				}
				return {
					id,
					frameCount: frames.length,
					railFlips,
					heddleFlips,
					heightMoves,
					scrollYSpread: [
						Math.min(...frames.map((f) => f.scrollY)),
						Math.max(...frames.map((f) => f.scrollY))
					],
					labels: [...new Set(frames.map((f) => f.label))]
				};
			}, headingId);
			verdicts.push(result);
			await page.screenshot({ path: `${OUT}/${headingId}-hold.png` });
		}

		await import('node:fs/promises').then((fs) =>
			fs.writeFile(`${OUT}/verdicts.json`, JSON.stringify(verdicts, null, 2))
		);
		for (const v of verdicts) {
			console.log(
				`${v.id ?? '??'}: frames=${v.frameCount ?? 0} railFlips=${v.railFlips ?? '?'} ` +
					`heddleFlips=${v.heddleFlips ?? '?'} heightMoves=${v.heightMoves ?? '?'} ` +
					`labels=${JSON.stringify(v.labels ?? [])} ${v.error ?? ''}`
			);
		}
		const flapping = verdicts.some((v) => (v.railFlips ?? 0) + (v.heddleFlips ?? 0) > 2);
		console.log(flapping ? 'VERDICT: FLAPPING (bug present)' : 'VERDICT: STEADY (no oscillation)');
		await browser.close();
		process.exitCode = flapping ? 2 : 0;
	} finally {
		vite.kill();
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
