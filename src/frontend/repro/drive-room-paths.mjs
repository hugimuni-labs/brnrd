// THE MAP THAT DREW ONE LEAF — the acceptance driver for `room.paths`.
//
// The room grew terrain by mining paths out of `edge.detail`, and a
// 142-boundary run rendered as a near-empty trie with one file on it
// (maintainer, 2026-08-28: "the map rendered is whaaaaa, compared to the
// actual edits and reads you have made so far over this run").
//
// This drives the fix against **the running checkout's own git state**, not
// a fixture: whatever this working tree has touched since its fork point is
// what the room is asked to draw, and the assert is that every one of those
// paths reaches the board as a leaf. A fixture could not have caught the
// original defect — the defect was that the real answer never arrived — so
// the instrument reads the real answer.
//
// Usage: node repro/drive-room-paths.mjs
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import { execSync } from 'node:child_process';
const PORT = 5202,
	OUT = '/tmp/paths-drive';
mkdirSync(OUT, { recursive: true });

// git's own answer, exactly as `cloud_publisher._run_paths` computes it
function attestedPaths() {
	const head = execSync(
		'git symbolic-ref --quiet refs/remotes/origin/HEAD || echo refs/remotes/origin/main'
	)
		.toString()
		.trim()
		.replace('refs/remotes/', '');
	const base = execSync(`git merge-base HEAD ${head}`).toString().trim();
	const tracked = execSync(`git diff --name-only ${base} --`).toString();
	const untracked = execSync('git ls-files --others --exclude-standard').toString();
	return [...new Set([...tracked.split('\n'), ...untracked.split('\n')])]
		.map((l) => l.trim())
		.filter((l) => l && !l.startsWith('.brr/'))
		.slice(0, 64);
}
const PATHS = attestedPaths();
if (PATHS.length === 0) {
	console.error('nothing touched since the fork point — nothing to assert');
	process.exit(0);
}
const now = '2026-08-28T22:30:00Z';
const run = {
	run_id: 'run-260828-2135-znki',
	name: 'the-settings-block-that-stands-alone',
	status: 'running',
	started_at: '2026-08-28T21:35:00Z',
	mood_rest: 'b·_·d',
	repo_label: 'hugimuni-labs/brnrd',
	kind: 'resident',
	card_text: '## Plan\n- [x] the bench\n- [x] the block\n- [ ] the paths',
	room: { env: 'host', branch: 'brr/the-paths-the-daemon-can-attest', dir: null, paths: PATHS },
	edge: {
		at: now,
		phase: 'post-tool',
		act: 'mutate',
		tools: ['Bash'],
		detail: "python3 - <<'PY' …",
		out_bytes: 42,
		injected: false,
		dir: '.'
	},
	crossings: [],
	relics_counts: { commit: 2 }
};
const ROUTES = {
	'/v1/dashboard/live-runs': {
		generated_at: now,
		runs: [run],
		stale: false,
		reported_at: now,
		spawn_max_concurrent: 8
	},
	'/v1/dashboard/run-ledger': { rows: [] },
	'/v1/dashboard/scheduled-wakes': { wakes: [] },
	'/v1/dashboard/quota': { generated_at: now, runner_quotas: [] }
};
const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	stdio: ['ignore', 'pipe', 'pipe']
});
vite.stdout.on('data', () => {});
vite.stderr.on('data', () => {});
for (let i = 0; i < 90; i++) {
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
	const body = ROUTES[new URL(route.request().url()).pathname];
	await route.fulfill({
		status: body ? 200 : 404,
		contentType: 'application/json',
		body: JSON.stringify(body ?? {})
	});
});
await page.goto(`http://localhost:${PORT}/ascii`, { waitUntil: 'networkidle' });
await page.waitForSelector('pre.board', { timeout: 15000 });
await delay(2500);
const board = await page.locator('pre.board').innerText();
// CLIPPED IS NOT ABSENT. A label the grid had no room for renders as
// `roomTopolo…` — the leaf is on the board and says so. A label that never
// reached the board renders as nothing. The first is the camera doing its
// job; the second is the defect this instrument exists to catch, and an
// assert that cannot tell them apart would fail on a working render and
// pass on a broken one the day a name got short.
const names = PATHS.map((p) => p.split('/').pop());
const whole = [];
const clipped = [];
const absent = [];
for (const name of names) {
	if (board.includes(name)) whole.push(name);
	// the shortest prefix the camera could have kept, plus its ellipsis
	else if (
		[...Array(name.length - 3).keys()].some((i) => board.includes(name.slice(0, i + 4) + '…'))
	)
		clipped.push(name);
	else absent.push(name);
}
console.log('attested :', PATHS.length);
console.log('whole    :', whole.length, '->', whole.sort().join(', ') || '(none)');
console.log('clipped  :', clipped.length, '->', clipped.sort().join(', ') || '(none)');
if (absent.length) {
	console.error('ABSENT FROM THE BOARD:', absent.join(', '));
	process.exitCode = 1;
} else {
	console.log('✓ every attested path reached the board as a leaf');
}
console.log(
	board
		.split('\n')
		.filter((l) => /\//.test(l) && !/THE SEA|drag/.test(l))
		.slice(0, 8)
		.join('\n')
);
await page.screenshot({ path: `${OUT}/map.png` });
await browser.close();
vite.kill('SIGTERM');
