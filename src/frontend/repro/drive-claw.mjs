// Drives THE CROSSING against the demo replay: a letter lifted from HOME and
// carried to the actor. Samples the board every 160ms (the page's own motion
// ticker) and reports, per frame, whether an arm and a letter are on it —
// because a still screenshot cannot tell "too fast" from "not running", and
// this ceremony is the one thing on the surface meant to be watched.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir, writeFile } from 'node:fs/promises';

const OUT = process.env.REPRO_OUT ?? '/tmp/claw-drive';
const PORT = 5199;

async function waitForServer(url, tries = 90) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok || res.status === 404) return;
		} catch {
			/* not up */
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

const boardText = (page) =>
	page.evaluate(() => document.querySelector('pre.board')?.textContent ?? '');

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	vite.stdout.on('data', () => {});
	vite.stderr.on('data', () => {});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		// Headless Chromium reports prefers-reduced-motion: reduce, and this
		// surface honours it — every ceremony would settle instantly and a
		// screenshot would "confirm" a dead frame.
		const ctx = await browser.newContext({
			viewport: { width: 1400, height: 900 },
			reducedMotion: 'no-preference'
		});
		const page = await ctx.newPage();
		await page.goto(`http://localhost:${PORT}/ascii?demo`, { waitUntil: 'networkidle' });

		const frames = [];
		let widest = 0;
		// The sampler is slower than the 160ms motion ticker — a board read is
		// a round trip — so this counts *whether* the ceremony ran and changed,
		// never how many of its frames existed. `CROSSING_TICKS` owns that.
		for (let i = 0; i < 220; i++) {
			const text = await boardText(page);
			// `┈` is the claw's own mark and nothing else uses it. The first
			// version of this driver counted `─` and `◇` — the corridor glyph
			// and the gate's pending marker — and reported a moving claw off a
			// board that had none. A measurement that cannot fail is not one.
			const arm = (text.match(/┈/g) ?? []).length;
			const letters = (text.match(/◇/g) ?? []).length;
			frames.push({ i, arm, letters });
			// Shoot the widest reach seen, not the first frame with any mark —
			// the ceremony is ~3.4s inside a much longer replay and the first
			// catch is usually a one-cell stub.
			if (arm > widest) {
				widest = arm;
				await page.screenshot({ path: `${OUT}/reach.png`, fullPage: false });
			}
			await delay(90);
		}
		await page.screenshot({ path: `${OUT}/final.png`, fullPage: false });
		await writeFile(`${OUT}/frames.json`, JSON.stringify(frames, null, 1));

		const withArm = frames.filter((f) => f.arm > 0).length;
		const withLetter = frames.filter((f) => f.letters > 0).length;
		const armWidths = [...new Set(frames.map((f) => f.arm))].sort((a, b) => a - b);
		console.log(
			JSON.stringify(
				{
					sampled: frames.length,
					framesWithArm: withArm,
					framesWithLetter: withLetter,
					distinctArmWidths: armWidths.length,
					// The claim a still shot cannot make: the arm changed length
					// across frames, so it is extending and withdrawing rather
					// than being painted once and left there.
					armAnimates: armWidths.length > 2,
					armIsSometimesAbsent: frames.some((f) => f.arm === 0),
					// Deliberately not reported: whether the letter "flies".
					// `◇` is the letter *and* the gate's pending marker, and
					// sharing that glyph is correct — they are the same object.
					// It therefore cannot be isolated from board text, and a
					// number that cannot fail is worse than no number.
					lettersOnBoard: withLetter,
					widestReach: widest
				},
				null,
				1
			)
		);
		await browser.close();
	} finally {
		vite.kill('SIGTERM');
	}
	console.log(`shots → ${OUT}`);
}
main().catch((e) => {
	console.error(e);
	process.exit(1);
});
