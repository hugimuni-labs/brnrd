// Pixel-diff two screenshots, using the browser we already have as the PNG
// decoder (no ImageMagick, no sharp — nothing new in devDependencies for a
// job the driver already ships a chromium for).
//
// Why not `cmp`: this dashboard paints a live clock, elapsed ages, and a
// mood face that animates, so two captures of an *unchanged* page differ by
// a hundred bytes every time. A byte compare says "differs" and means
// nothing. A pixel count with a per-channel tolerance says how much, and
// where — and `--ignore` blanks the regions that are honestly expected to
// move, so what is left is signal.
//
//   node repro/diff-shots.mjs before.png after.png [out-diff.png] \
//        [--tolerance 12] [--ignore x,y,w,h]...
//
// Exit 0 always — this reports, it does not judge. The number is the point.

import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const positional = [];
const ignore = [];
let tolerance = 12;
for (let i = 0; i < args.length; i++) {
	if (args[i] === '--tolerance') tolerance = Number(args[++i]);
	else if (args[i] === '--ignore') ignore.push(args[++i].split(',').map(Number));
	else positional.push(args[i]);
}
const [beforePath, afterPath, outPath] = positional;
if (!beforePath || !afterPath) {
	console.error('usage: node repro/diff-shots.mjs before.png after.png [diff.png]');
	process.exit(2);
}

const b64 = (p) => `data:image/png;base64,${readFileSync(p).toString('base64')}`;

const browser = await chromium.launch();
const page = await browser.newPage();
const result = await page.evaluate(
	async ({ a, b, tolerance, ignore }) => {
		const load = (src) =>
			new Promise((resolve, reject) => {
				const img = new Image();
				img.onload = () => resolve(img);
				img.onerror = reject;
				img.src = src;
			});
		const [ia, ib] = await Promise.all([load(a), load(b)]);
		const w = Math.max(ia.width, ib.width);
		const h = Math.max(ia.height, ib.height);
		const grab = (img) => {
			const c = document.createElement('canvas');
			c.width = w;
			c.height = h;
			const ctx = c.getContext('2d', { willReadFrequently: true });
			ctx.drawImage(img, 0, 0);
			return ctx.getImageData(0, 0, w, h);
		};
		const da = grab(ia).data;
		const db = grab(ib).data;
		const out = document.createElement('canvas');
		out.width = w;
		out.height = h;
		const octx = out.getContext('2d');
		const odata = octx.createImageData(w, h);
		const masked = (x, y) =>
			ignore.some(([mx, my, mw, mh]) => x >= mx && x < mx + mw && y >= my && y < my + mh);
		let differing = 0;
		let compared = 0;
		let minX = w;
		let minY = h;
		let maxX = -1;
		let maxY = -1;
		for (let y = 0; y < h; y++) {
			for (let x = 0; x < w; x++) {
				const i = (y * w + x) * 4;
				if (masked(x, y)) {
					odata.data[i] = 40;
					odata.data[i + 1] = 40;
					odata.data[i + 2] = 40;
					odata.data[i + 3] = 255;
					continue;
				}
				compared++;
				const d =
					Math.abs(da[i] - db[i]) +
					Math.abs(da[i + 1] - db[i + 1]) +
					Math.abs(da[i + 2] - db[i + 2]);
				if (d > tolerance) {
					differing++;
					if (x < minX) minX = x;
					if (y < minY) minY = y;
					if (x > maxX) maxX = x;
					if (y > maxY) maxY = y;
					odata.data[i] = 255;
					odata.data[i + 1] = 0;
					odata.data[i + 2] = 0;
					odata.data[i + 3] = 255;
				} else {
					const g = Math.round(da[i] * 0.25);
					odata.data[i] = g;
					odata.data[i + 1] = g;
					odata.data[i + 2] = g;
					odata.data[i + 3] = 255;
				}
			}
		}
		octx.putImageData(odata, 0, 0);
		return {
			width: w,
			height: h,
			sizeMatch: ia.width === ib.width && ia.height === ib.height,
			dimensions: { before: [ia.width, ia.height], after: [ib.width, ib.height] },
			differing,
			compared,
			percent: compared === 0 ? 0 : (differing / compared) * 100,
			bbox: maxX < 0 ? null : { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 },
			png: out.toDataURL('image/png')
		};
	},
	{ a: b64(beforePath), b: b64(afterPath), tolerance, ignore }
);
await browser.close();

if (outPath) {
	writeFileSync(outPath, Buffer.from(result.png.split(',')[1], 'base64'));
}
const { png, ...report } = result;
void png;
console.log(JSON.stringify(report, null, 1));
