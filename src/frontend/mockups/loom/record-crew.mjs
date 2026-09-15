import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { chromium } from 'playwright';
import { setTimeout as delay } from 'node:timers/promises';
import { closeBrowser, runDriver } from '../../repro/finish.mjs';

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const projectRoot = fileURLToPath(new URL('../../../../', import.meta.url));
const outputDir = join(projectRoot, 'media', 'loom', 'crew-scene');
const python = join(projectRoot, '.venv', 'bin', 'python');
const ffmpeg = '/opt/homebrew/bin/ffmpeg';
const mp4 = join(outputDir, 'crew-scene-1280x720.mp4');
const gif = join(outputDir, 'crew-scene-640x360.gif');
const sheet = join(outputDir, 'crew-scene-contact-sheet.png');
const timestamps = ['0.5', '3.0', '5.5', '8.5', '11.5', '14.0', '17.0', '19.0'];

async function command(file, args) {
	const { stderr } = await run(file, args, { maxBuffer: 10 * 1024 * 1024 });
	if (stderr) process.stderr.write(stderr);
}

async function main() {
	await mkdir(outputDir, { recursive: true });
	const scratch = await mkdtemp(join(tmpdir(), 'brnrd-crew-scene-'));
	const rawDir = join(scratch, 'raw');
	const framesDir = join(scratch, 'frames');
	await mkdir(rawDir);
	await mkdir(framesDir);

	let browser;
	let context;
	try {
		browser = await chromium.launch();
		context = await browser.newContext({
			viewport: { width: 1280, height: 720 },
			recordVideo: { dir: rawDir, size: { width: 1280, height: 720 } }
		});
		const page = await context.newPage();
		await page.goto(pathToFileURL(join(here, 'crew-scene.html')).href, {
			waitUntil: 'networkidle'
		});
		await page.locator('#crew-scene').waitFor({ state: 'visible' });
		const video = page.video();
		await delay(22_000);
		await context.close();
		context = undefined;
		const rawVideo = await video.path();

		await command(ffmpeg, [
			'-y',
			'-i',
			rawVideo,
			'-c:v',
			'libx264',
			'-preset',
			'medium',
			'-crf',
			'20',
			'-pix_fmt',
			'yuv420p',
			'-movflags',
			'+faststart',
			'-an',
			mp4
		]);

		await command(ffmpeg, [
			'-y',
			'-i',
			mp4,
			'-filter_complex',
			'fps=15,scale=640:360:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=3',
			'-loop',
			'0',
			gif
		]);

		const frames = [];
		for (const [index, timestamp] of timestamps.entries()) {
			const frame = join(framesDir, `frame-${String(index + 1).padStart(2, '0')}.png`);
			frames.push(frame);
			await command(ffmpeg, [
				'-y',
				'-ss',
				timestamp,
				'-i',
				mp4,
				'-frames:v',
				'1',
				'-vf',
				'scale=640:360:flags=lanczos',
				frame
			]);
		}

		await command(python, [join(here, 'contact-sheet.py'), sheet, ...frames]);
		for (const path of [mp4, gif, sheet]) console.log(path);
	} finally {
		if (context) await Promise.race([context.close(), delay(15_000)]);
		if (browser) await closeBrowser(browser, 'record-crew');
		await rm(scratch, { recursive: true, force: true });
	}
}

runDriver(main);
