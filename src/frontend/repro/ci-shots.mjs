// The CI-facing wrapper around deliberately small, human-runnable repro
// drivers. It captures each driver on a selected checkout, then produces the
// diff summary and Markdown comment that Actions publishes.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

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
const run = (program, argv, cwd) => {
	const result = spawnSync(program, argv, {
		cwd,
		encoding: 'utf8',
		stdio: ['ignore', 'pipe', 'pipe']
	});
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
		const stdout = run(
			process.execPath,
			[driver, '--out', driverOut, '--port', String(portStart + index)],
			root
		);
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
	const lines = [
		'<!-- ui-shots -->',
		'## UI screenshots',
		'',
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
	if (!pairs) process.exit(0);
	const output = value('--output');
	if (output) writeFileSync(output, `${lines.join('\n')}\n`);
	else console.log(lines.join('\n'));
} else {
	console.error('usage: ci-shots.mjs <capture|diff|comment> ...');
	process.exit(2);
}
