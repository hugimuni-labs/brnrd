// Drive the generated ground: /new?demo replays a shaped day over the real
// repo map — the acceptance is that districts EXIST only as fog until the
// replay's own dirs touch them (fog = attention), the garage seats its
// waiting bodies, strand plots wear their branch signage, and the act
// stations light with the resident's current act. Probes over stills where
// a still could lie.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const PORT = 5197;
const OUT = process.env.REPRO_OUT ?? '/tmp/ground-drive';

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
		await page.goto(`http://localhost:${PORT}/new?demo`, { waitUntil: 'networkidle' });

		const groundState = () =>
			page.evaluate(() => {
				const districts = Array.from(document.querySelectorAll('.district')).map((el) => ({
					fog: el.classList.contains('fog-lit')
						? 'lit'
						: el.classList.contains('fog-explored')
							? 'explored'
							: 'void',
					name: el.querySelector('.district-name')?.textContent ?? null
				}));
				return {
					districts,
					lit: districts.filter((d) => d.fog === 'lit').length,
					named: districts.filter((d) => d.name).length,
					seats: document.querySelectorAll('.seat-t').length,
					plots: document.querySelectorAll('.plot').length,
					branches: Array.from(document.querySelectorAll('.plot-branch')).map(
						(el) => el.textContent?.trim() ?? ''
					),
					litStation: document.querySelector('.station.lit .station-label')?.textContent ?? null,
					stations: document.querySelectorAll('.station').length
				};
			});

		// Early: the wake frame — fog mostly closed, docs lit by the orient.
		await delay(4200);
		const early = await groundState();
		await page.screenshot({ path: `${OUT}/0-early-fog.png` });
		if (early.districts.length === 0) throw new Error('no districts dealt');
		if (early.lit === 0) throw new Error('the first act lit nothing — fog never opens');
		if (early.lit >= early.districts.length) {
			throw new Error('everything lit at wake — fog of war is not fog');
		}
		if (early.seats !== 2) throw new Error(`garage expected 2 seats, saw ${early.seats}`);
		if (early.stations !== 5) throw new Error(`expected 5 act stations, saw ${early.stations}`);
		console.log(
			`early: ${early.districts.length} districts · ${early.lit} lit · ` +
				`${early.seats} seats · station=${early.litStation}`
		);

		// Mid-replay: strands up — plots + branch signage + more fog lifted.
		await delay(11000);
		const mid = await groundState();
		await page.screenshot({ path: `${OUT}/1-mid-plots.png` });
		if (mid.plots === 0) throw new Error('no strand plots staked');
		if (!mid.branches.some((b) => b.startsWith('brr/'))) {
			throw new Error(`no branch signage on plots: ${JSON.stringify(mid.branches)}`);
		}
		console.log(
			`mid: plots=${mid.plots} branches=${mid.branches.join(',')} · ` +
				`lit=${mid.lit} station=${mid.litStation}`
		);

		// Late: near closeout — the day's whole path explored or lit. The
		// replay's last real dirs (src/brr, tests) land at ~25-29s, so the
		// growth assertion belongs here, not at mid.
		await delay(13500);
		const late = await groundState();
		await page.screenshot({ path: `${OUT}/2-late-atlas.png` });
		const lateKnown = late.lit + late.districts.filter((d) => d.fog === 'explored').length;
		if (lateKnown <= early.lit) {
			throw new Error('the map did not grow — attention is not spreading');
		}
		console.log(
			`late: known=${lateKnown} lit=${late.lit} named=${late.named}/${late.districts.length}`
		);

		// Phone width — the map must still read.
		const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
		const p2 = await phone.newPage();
		await p2.goto(`http://localhost:${PORT}/new?demo`, { waitUntil: 'networkidle' });
		await delay(8200);
		await p2.screenshot({ path: `${OUT}/3-phone.png` });
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
