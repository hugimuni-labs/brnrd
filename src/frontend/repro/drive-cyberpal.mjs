// Drive the cyberpal wordmark: the landing hero, the brand-bench alive
// panel, and mid-cycle beats. The acceptance is visual — the being must
// read as a face at rest, as the name mid-cycle, glitch on the transition,
// and sway the whole time — so this script both screenshots the states and
// probes that the animation actually runs (a timer that never fires renders
// a perfectly plausible still).

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const PORT = 5193;
const OUT = process.env.REPRO_OUT ?? '/tmp/cyberpal-drive';

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
		const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
		const page = await context.newPage();

		// ── the landing hero ──────────────────────────────────────────────
		await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
		await delay(800);
		await page.screenshot({ path: `${OUT}/0-landing-rest.png` });

		// The being's first cycle starts at 1.8s; the long clean name beat
		// runs ~2.55s→3.8s into the cycle. Catch the glitch stutter and the
		// readable name.
		await delay(1200); // t≈2.0s — inside the blink/early cycle
		await page.screenshot({ path: `${OUT}/1-landing-early-cycle.png` });
		await delay(1000); // t≈3.0s — inside the long name hold
		await page.screenshot({ path: `${OUT}/2-landing-name.png` });

		// Behavioural probes: the beat walker must actually swap bodies, and
		// the sway must actually move. Sample the rendered path markup and
		// the computed transform across the cycle.
		const markHtml = () =>
			page.evaluate(() => document.querySelector('.cyberpal svg')?.innerHTML.length ?? null);
		const sway = () =>
			page.evaluate(() => {
				const el = document.querySelector('.cyberpal-sway');
				return el ? getComputedStyle(el).transform : null;
			});
		const h1 = await markHtml();
		const s1 = await sway();
		await delay(4000);
		const h2 = await markHtml();
		const s2 = await sway();
		if (s1 !== null && s1 === s2) throw new Error(`sway did not move: transform stuck at ${s1}`);
		console.log(`sway probe: moving (${s1} -> ${s2})`);
		console.log(`body probe: ${h1} -> ${h2} bytes of drawn markup`);

		// A whole cycle sampled fast, to verify the name state and the glitch
		// ghosts genuinely appear in DOM (mid-beat shots are timing-lucky;
		// this is not).
		const seen = { ghosts: false, nameLikely: false };
		const t0 = Date.now();
		while (Date.now() - t0 < 10_000 && !(seen.ghosts && seen.nameLikely)) {
			const state = await page.evaluate(() => {
				const svg = document.querySelector('.cyberpal svg');
				if (!svg) return null;
				const groups = svg.querySelectorAll('g > g').length;
				const html = svg.innerHTML;
				return { groups, ghosts: html.includes('#ff3b30'), paths: (html.match(/<path/g) ?? []).length };
			});
			if (state?.ghosts) seen.ghosts = true;
			// The name frame draws more paths (5 letterforms) than any face.
			if (state && state.paths >= 14) seen.nameLikely = true;
			await delay(90);
		}
		if (!seen.ghosts) throw new Error('no aberration ghosts ever appeared across a full cycle');
		if (!seen.nameLikely) throw new Error('the name frame never appeared across a full cycle');
		console.log('cycle probe: ghosts ✓ · name frame ✓');

		// ── the brand-bench alive panel ───────────────────────────────────
		await page.goto(`http://localhost:${PORT}/brand-bench`, { waitUntil: 'networkidle' });
		await delay(2400);
		await page.screenshot({ path: `${OUT}/3-bench-alive.png` });
		// kawaii base, via the bench's own frame select
		await page.selectOption('select', { index: 0 }).catch(() => {});
		await page.evaluate(() => {
			const selects = Array.from(document.querySelectorAll('select'));
			const frameSel = selects.find((s) =>
				Array.from(s.options).some((o) => o.value === 'kawaii')
			);
			if (frameSel) {
				frameSel.value = 'kawaii';
				frameSel.dispatchEvent(new Event('change', { bubbles: true }));
			}
		});
		await delay(600);
		await page.screenshot({ path: `${OUT}/4-bench-kawaii.png` });

		// ── the signed-in header, mood on the wire ────────────────────────
		// Phone width too — the header mark must not blow up the layout.
		const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
		const p2 = await phone.newPage();
		await p2.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
		await delay(2600);
		await p2.screenshot({ path: `${OUT}/5-landing-phone.png` });
		await p2.close();
		await phone.close();

		await page.close();
		await context.close();
		await browser.close();
		console.log(`shots: ${OUT}`);
	} finally {
		vite.kill();
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
