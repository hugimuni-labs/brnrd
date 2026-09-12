// The CI-facing wrapper around deliberately small, human-runnable repro
// drivers. It captures each driver on a selected checkout, then produces the
// diff summary and Markdown comment that Actions publishes.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { classifyCoverage, coverageHeadline, renderCoverage } from './shot-coverage.mjs';

const [command, ...args] = process.argv.slice(2);
const value = (name, fallback = null) => {
	const index = args.indexOf(name);
	return index === -1 ? fallback : (args[index + 1] ?? fallback);
};
const manifestPath = value(
	'--manifest',
	join(dirname(new URL(import.meta.url).pathname), 'ci-shots.json')
);
const manifest = () => JSON.parse(readFileSync(manifestPath, 'utf8'));
// Per-driver ceiling. A driver boots a vite server and takes a handful of
// screenshots; on this laptop all three finish inside two minutes. Without a
// bound, one that waits on a selector or a server that never binds hangs the
// whole job until GitHub's 6-hour default — and a job that never finishes
// posts no comment, never goes red, and is therefore never read. Measured
// 2026-09-12: of the sixteen `ui-shots` runs that have ever existed, ten were
// cancelled by the next push and six were still hanging in
// `Capture before and after`. Not one has ever succeeded. The clause they
// back was signed 2026-08-30.
const DRIVER_TIMEOUT_MS = Number(process.env.CI_SHOTS_DRIVER_TIMEOUT_MS || 240_000);

const run = (program, argv, cwd, { timeout = 0 } = {}) => {
	const result = spawnSync(program, argv, {
		cwd,
		encoding: 'utf8',
		// stdout is captured — it is the JSON payload the summary embeds.
		// stderr is *inherited*, deliberately: buffering it means a child that
		// is killed at the timeout contributes nothing to the log, and that is
		// precisely the case worth reading. Measured on this PR's own run —
		// `drive-fuel.mjs` was SIGKILLed at 240 s having printed, as far as the
		// log could tell, absolutely nothing in four minutes. It had printed;
		// the buffer was thrown away with the process.
		stdio: ['ignore', 'pipe', 'inherit'],
		timeout: timeout || undefined,
		killSignal: 'SIGKILL'
	});
	// `spawnSync` reports a timeout as `signal` + `error`, never as a status —
	// checking only `status !== 0` reads a killed child as a clean run and
	// returns its partial stdout, which is the same silence one layer in.
	if (result.error || result.signal)
		throw new Error(
			`${program} ${argv.join(' ')} did not finish` +
				(result.signal ? ` (killed with ${result.signal} after ${timeout}ms)` : '') +
				// stderr went straight to this process's own stderr, so it is
				// already above this line in the log — saying so beats printing
				// an empty string and looking like the child said nothing.
				`. Its own output is inlined above.\n${result.stdout || result.error?.message || ''}`
		);
	if (result.status !== 0)
		throw new Error(`${program} ${argv.join(' ')} failed:\n${result.stderr || result.stdout}`);
	return result.stdout;
};

if (command === 'capture') {
	const root = resolve(value('--root', process.cwd()));
	const out = resolve(value('--out'));
	const portStart = Number(value('--port-start', '5197'));
	const result = { root, drivers: [] };
	mkdirSync(out, { recursive: true });
	for (const [index, entry] of manifest().entries()) {
		const driver = join(root, 'repro', entry.driver);
		if (!existsSync(driver)) {
			result.drivers.push({
				...entry,
				status: 'skipped',
				reason: `missing from checkout: repro/${entry.driver}`
			});
			continue;
		}
		const driverOut = join(out, entry.driver.replace(/\.mjs$/, ''));
		// Progress to stderr, so a hung run says *which* driver it is hung in.
		// The old loop buffered every driver's stdout and printed one JSON blob
		// at the end, so a job stuck here reported nothing at all — the six
		// hung runs above are, in their own logs, a step name and silence.
		const startedAt = Date.now();
		process.stderr.write(`[ci-shots] capture ${entry.driver} (root=${root})\n`);
		const stdout = run(
			process.execPath,
			[driver, '--out', driverOut, '--port', String(portStart + index)],
			root,
			{ timeout: DRIVER_TIMEOUT_MS }
		);
		process.stderr.write(`[ci-shots] ✓ ${entry.driver} in ${Date.now() - startedAt}ms\n`);
		result.drivers.push({ ...entry, status: 'captured', out: driverOut, stdout });
	}
	writeFileSync(join(out, 'capture.json'), `${JSON.stringify(result, null, 2)}\n`);
	console.log(JSON.stringify(result, null, 2));
} else if (command === 'diff') {
	const before = resolve(value('--before'));
	const after = resolve(value('--after'));
	const out = resolve(value('--out'));
	const summary = { drivers: [] };
	mkdirSync(out, { recursive: true });
	for (const entry of manifest()) {
		const driver = entry.driver.replace(/\.mjs$/, '');
		const row = { driver: entry.driver, shots: [] };
		for (const shot of entry.shots) {
			const beforePath = join(before, driver, shot);
			const afterPath = join(after, driver, shot);
			const diffPath = join(out, driver, shot);
			if (!existsSync(beforePath) || !existsSync(afterPath)) {
				row.shots.push({
					shot,
					status: 'skipped',
					reason: !existsSync(beforePath)
						? 'not produced by base checkout'
						: 'not produced by PR head'
				});
				continue;
			}
			mkdirSync(dirname(diffPath), { recursive: true });
			const result = JSON.parse(
				run(
					process.execPath,
					[
						join(dirname(new URL(import.meta.url).pathname), 'diff-shots.mjs'),
						beforePath,
						afterPath,
						diffPath
					],
					process.cwd()
				)
			);
			row.shots.push({
				shot,
				status: 'diffed',
				beforePath,
				afterPath,
				diffPath,
				pixels: result.differing
			});
		}
		summary.drivers.push(row);
	}
	const output = value('--output', join(out, 'summary.json'));
	writeFileSync(output, `${JSON.stringify(summary, null, 2)}\n`);
	console.log(JSON.stringify(summary, null, 2));
} else if (command === 'comment') {
	const summary = JSON.parse(readFileSync(value('--summary'), 'utf8'));
	const base = value('--base');
	const sha = value('--sha');
	const urlBase = value(
		'--url-base',
		'https://raw.githubusercontent.com/hugimuni-labs/brnrd/shots/pr-PLACEHOLDER/SHA'
	);
	const url = (path) => `${urlBase}/${path.split('/').map(encodeURIComponent).join('/')}`;

	// Did this check look at what the PR changed? (#1944)
	//
	// Two inputs, both receipts rather than beliefs: the files the PR touched
	// under `src/frontend`, and the source modules each captured page actually
	// fetched from the dev server while its shots were taken. The second is
	// written by `moduleRecorder` beside the shots; a driver that produced no
	// record contributes nothing, and if *no* driver produced one the verdict
	// is `unknown` rather than a confident accusation from an empty set.
	const changedFile = value('--changed');
	const changed =
		changedFile && existsSync(changedFile)
			? readFileSync(changedFile, 'utf8')
					.split('\n')
					.map((line) => line.trim())
					.filter(Boolean)
			: [];
	const afterDir = value('--after');
	const exercised = new Set();
	const loaded = new Set();
	let records = 0;
	if (afterDir)
		for (const entry of manifest()) {
			const record = join(resolve(afterDir), entry.driver.replace(/\.mjs$/, ''), 'modules.json');
			if (!existsSync(record)) continue;
			try {
				const parsed = JSON.parse(readFileSync(record, 'utf8'));
				for (const file of parsed.exercised ?? []) exercised.add(file);
				for (const file of parsed.loaded ?? []) loaded.add(file);
				records += 1;
			} catch (err) {
				process.stderr.write(`[ci-shots] unreadable module record ${record}: ${err.message}\n`);
			}
		}
	const allIdentical = summary.drivers.every((driver) =>
		driver.shots.filter((shot) => shot.status === 'diffed').every((shot) => shot.pixels === 0)
	);
	const coverage = classifyCoverage({
		changed,
		exercised: [...exercised],
		loaded: [...loaded],
		coverageKnown: records > 0
	});
	const headline = coverageHeadline(coverage, { allIdentical });

	const lines = [
		'<!-- ui-shots -->',
		'## UI screenshots',
		'',
		...(headline ? [headline, ''] : []),
		`Head: \`${sha}\` · merge-base: \`${base}\``,
		''
	];
	let pairs = 0;
	for (const driver of summary.drivers) {
		const shots = driver.shots.filter((shot) => shot.status === 'diffed');
		if (!shots.length) continue;
		pairs += shots.length;
		lines.push(
			`### \`${driver.driver}\``,
			'',
			'| shot | before | after | diff |',
			'| --- | --- | --- | --- |'
		);
		for (const shot of shots) {
			if (shot.pixels === 0) {
				lines.push(`| ${shot.shot} | unchanged | unchanged | 0 px changed |`);
				continue;
			}
			const before = url(`before/${driver.driver.replace(/\.mjs$/, '')}/${shot.shot}`);
			const after = url(`after/${driver.driver.replace(/\.mjs$/, '')}/${shot.shot}`);
			const diff = url(`diff/${driver.driver.replace(/\.mjs$/, '')}/${shot.shot}`);
			lines.push(
				`| ${shot.shot} | <img width="320" src="${before}" /> | <img width="320" src="${after}" /> | <a href="${diff}">${shot.pixels} px changed</a> |`
			);
		}
		lines.push('');
	}
	for (const driver of summary.drivers)
		for (const shot of driver.shots.filter((item) => item.status === 'skipped'))
			lines.push(`_Skipped \`${driver.driver}\` / ${shot.shot}: ${shot.reason}._`, '');

	const surfaces = summary.drivers.flatMap((driver) =>
		driver.shots
			.filter((shot) => shot.status === 'diffed')
			.map((shot) => `\`${driver.driver.replace(/\.mjs$/, '')}/${shot.shot}\``)
	);
	if (surfaces.length || changed.length)
		lines.push(...renderCoverage(coverage, { surfaces, allIdentical }));

	if (!pairs) process.exit(0);
	const output = value('--output');
	if (output) writeFileSync(output, `${lines.join('\n')}\n`);
	else console.log(lines.join('\n'));
} else {
	console.error('usage: ci-shots.mjs <capture|diff|comment> ...');
	process.exit(2);
}
