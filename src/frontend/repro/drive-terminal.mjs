// THE TERMINAL IS A PLACE — the acceptance driver for rung 4.
//
// "commands should move to the terminal... which you kinda walk into, and
// stay below" (maintainer, 2026-08-28), with his dimensions from earlier the
// same day: "a window rendered on top of the camp, a few lines in height,
// about 50 in width".
//
// What it checks is that the window is a *window*: on the camp, exactly its
// declared size, every row starting and ending on a frame character. A
// panel that drifts a column per row is the failure this catches, and it is
// invisible to any assertion made on trimmed text — which is why the rows
// are sliced by frame column rather than trimmed.
//
// Usage: node repro/drive-terminal.mjs
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import { TERMINAL_COLS, TERMINAL_ROWS } from '../src/lib/roomTerminal.ts';
const PORT = 5203,
	OUT = '/tmp/terminal-drive';
mkdirSync(OUT, { recursive: true });
const CMDS = [
	['orient', "sed -n '1,60p' src/lib/roomTerminal.ts"],
	['mutate', "python3 - <<'PY' …"],
	['probe', 'npm test 2>&1 | grep -E "pass|fail"'],
	['mutate', "cat > src/lib/roomTerminal.test.ts <<'TS' …"],
	['orient', 'grep -n "kind === \'camp\'" src/lib/roomTopology.ts'],
	['publish', 'git commit -q -F - && git push -u origin brr/the-terminal-is-a-place']
];
let i = 0;
const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	stdio: ['ignore', 'pipe', 'pipe']
});
vite.stdout.on('data', () => {});
vite.stderr.on('data', () => {});
for (let k = 0; k < 90; k++) {
	try {
		const r = await fetch(`http://localhost:${PORT}/`);
		if (r.ok || r.status === 404) break;
	} catch {}
	await delay(500);
}
const browser = await chromium.launch();
const page = await browser.newPage({
	viewport: { width: 1280, height: 900 },
	deviceScaleFactor: 2
});
await page.route('**/v1/dashboard/**', async (route) => {
	const path = new URL(route.request().url()).pathname;
	let body = { rows: [], wakes: [], runner_quotas: [], generated_at: '2026-08-28T22:30:00Z' };
	if (path.endsWith('/live-runs')) {
		// cycle rather than clamp: a clamped fixture repeats its last row and
		// the repetition reads as a dedupe bug in the thing under test
		const [act, detail] = CMDS[i % CMDS.length];
		const at = `2026-08-28T22:${String(30 + i).padStart(2, '0')}:00Z`;
		i++;
		body = {
			generated_at: at,
			stale: false,
			reported_at: at,
			spawn_max_concurrent: 8,
			runs: [
				{
					run_id: 'run-260828-2135-znki',
					name: 'the-settings-block',
					status: 'running',
					started_at: '2026-08-28T21:35:00Z',
					mood_rest: 'b·_·d',
					repo_label: 'hugimuni-labs/brnrd',
					kind: 'resident',
					card_text: '## Plan\n- [x] a\n- [ ] b',
					room: {
						env: 'host',
						branch: 'brr/the-terminal-is-a-place',
						dir: null,
						paths: ['src/frontend/src/lib/roomTerminal.ts', 'src/frontend/src/lib/asciiCamera.ts']
					},
					edge: {
						at,
						phase: 'post-tool',
						act,
						tools: ['Bash'],
						detail,
						out_bytes: 42,
						injected: false,
						dir: '.'
					},
					crossings: [],
					relics_counts: { commit: 1 }
				}
			]
		};
	}
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
});
await page.goto(`http://localhost:${PORT}/ascii`, { waitUntil: 'networkidle' });
await page.waitForSelector('pre.board', { timeout: 15000 });
for (let k = 0; k < 7; k++) await delay(2200);
const board = await page.locator('pre.board').innerText();
// Slice the window out by its own frame columns rather than by trimming —
// leading sea and corridors differ per row, and `replace(/^\s+/, '')` would
// destroy the very alignment this is checking.
const rows = board.split('\n');
const top = rows.findIndex((l) => l.includes('┌ $ bench'));
if (top === -1) {
	console.error('ABSENT: no terminal window on the board');
	process.exit(1);
}
const left = rows[top].indexOf('┌');
const win = rows
	.slice(top, top + TERMINAL_ROWS + 2)
	.map((l) => l.slice(left, left + TERMINAL_COLS));
console.log(win.join('\n'));
const widths = new Set(win.map((l) => l.length));
const aligned = win.every((l) => /^[┌│└]/.test(l) && /[┐│┘]$/.test(l));
console.log('rows   :', win.length, '(expected', TERMINAL_ROWS + 2, ')');
console.log('widths :', [...widths].join(','));
console.log('aligned:', aligned);
if (win.length !== TERMINAL_ROWS + 2 || widths.size !== 1 || !aligned) {
	console.error('the window is not a window: rows/width/alignment broke');
	process.exitCode = 1;
} else {
	console.log('✓ a bounded window, on the camp, holding the labour');
}
await page.screenshot({ path: `${OUT}/terminal.png` });
await browser.close();
vite.kill('SIGTERM');
