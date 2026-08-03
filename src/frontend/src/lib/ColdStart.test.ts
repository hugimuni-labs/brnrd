import { deepEqual, equal, ok } from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { DOCS_URL } from './publicStats.ts';
import type { ConnectedRepo } from './repos.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ColdStart.svelte');
const generated = join(here, '.coldStart.generated.mjs');

// Same rendering dance as PublishConsentNotice.test.ts: compile the real
// component and render it with real props, so a claim that only becomes
// false in the rendered HTML still fails here.
async function renderColdStart(
	repos: ConnectedRepo[] | null,
	pairCommand: string | null = 'cd <repo>\nbrnrd account connect https://brnrd.dev\nbrnrd up'
): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'ColdStart' });
	const runnable = compiled.js.code
		.replace(/'\.\/publicStats'/g, "'./publicStats.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { repos, pairCommand } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function repo(over: Partial<ConnectedRepo> = {}): ConnectedRepo {
	return {
		id: 'repo-1',
		dispatch_default: false,
		repo_full_name: 'Gurio/brr',
		forge: 'github',
		forge_repo_id: null,
		repo_owner: 'Gurio',
		repo_name: 'brr',
		default_branch: null,
		created_at: null,
		updated_at: null,
		created_label: '',
		updated_label: '',
		daemon_count: 0,
		daemon_status: 'missing',
		daemon_label: '',
		daemon_last_seen: '',
		daemon_last_seen_at: null,
		latest_daemon_name: '',
		gates: [],
		setup_command: '',
		telegram_paired: false,
		environment_default: null,
		environments: [],
		publish_layers: null,
		github_bot_collaborator: null,
		github_bot_checked_at: null,
		github_bot_status: null,
		github_bot_marker_notice: null,
		github_bot_notice: null,
		...over
	};
}

// The reported gap (2026-08-03 signup): six empty sections and no install
// line, no pointer at the page where a repo is enabled, no docs link.
test('an account with nothing connected is told the three things that have to happen', async () => {
	const html = await renderColdStart([]);
	ok(html.includes('install the cli'), 'names the install step');
	ok(html.includes('npm install -g brnrd'), 'prints the headline install command');
	ok(html.includes('uv tool install brnrd'), 'offers the uv alternate');
	ok(html.includes('pipx install brnrd'), 'offers the pipx alternate');
	ok(html.includes('enable a repository'), 'names the repo-enablement step');
	ok(html.includes('href="/repos"'), 'links the page that step lives on');
	ok(html.includes('pair the daemon'), 'names the pairing step');
	ok(html.includes('brnrd account connect'), 'prints the pairing command');
	ok(html.includes(`href="${DOCS_URL}"`), 'carries a docs link');
});

// The failure mode worth a test of its own: a first-run panel that never
// leaves is worse than the blank page it replaced.
test('the block is gone the moment the account has a repo', async () => {
	const html = await renderColdStart([repo()]);
	ok(!html.includes('the cold start'));
	ok(!html.includes('npm install -g brnrd'));
	ok(!html.includes('nothing is paired yet'));
});

// `null` is "the repos fetch has not landed", not "this account is empty".
// Rendering the cold start on it would flash a first-run panel at an account
// with fifteen repos on every page load.
test('an unlanded repo list renders nothing rather than guessing empty', async () => {
	const html = await renderColdStart(null);
	ok(!html.includes('the cold start'));
	ok(!html.includes('npm install -g brnrd'));
});

// The pairing lines are backend-owned (`_session.pairing_command`, served as
// `pairing_command` on /v1/dashboard/repos). This component must render what
// it is handed and hold no second copy of its own — a local fallback string
// is exactly the drift the single source exists to prevent.
test('the pairing command is rendered from the prop, never restated in the component', async () => {
	const html = await renderColdStart(
		[],
		'cd <repo>\nbrnrd account connect https://elsewhere\nbrnrd up'
	);
	ok(html.includes('brnrd account connect https://elsewhere'));
	ok(!html.includes('https://brnrd.dev'), 'no hardcoded endpoint of its own');
	const source = readFileSync(componentPath, 'utf8');
	ok(!source.includes('brnrd account connect'), 'the command is not typed into the component');
});

// A missing command must not render an empty terminal box pretending to hold
// one; the step's prose still stands on its own.
test('a missing pairing command drops the code block, not the step', async () => {
	const html = await renderColdStart([], null);
	ok(html.includes('pair the daemon'), 'the step survives');
	ok(html.includes('In the checkout'), 'its prose survives');
	ok(!html.includes('brnrd account connect'), 'no terminal box pretending to hold a command');
});

// The product's only docs link used to be `gurio.github.io/brr/`, which
// returns 404 since the repo transfer — driven 2026-08-03, both hosts. It is
// one constant now precisely so it cannot rot per-surface again.
test('the docs constant points at the published site, not the retired host', async () => {
	ok(!DOCS_URL.includes('gurio.github.io'));
	equal(DOCS_URL, 'https://hugimuni-labs.github.io/brnrd/');
});

// Source-level, deliberately: the two signed-in routes are the whole
// dashboard and the whole repo manager, and compiling either to assert one
// header link would drag in forty components for a one-line claim. What is
// pinned is the property that was actually missing — that each signed-in
// surface reaches the docs at all, through the shared constant rather than
// its own literal.
const SIGNED_IN_ROUTES = [
	join(here, '..', 'routes', '+page.svelte'),
	join(here, '..', 'routes', 'repos', '+page.svelte')
];

test('every signed-in surface carries a docs link, bound to the shared constant', () => {
	for (const route of SIGNED_IN_ROUTES) {
		const source = readFileSync(route, 'utf8');
		ok(source.includes('href={DOCS_URL}'), `${route} links the docs`);
		ok(source.includes("from '$lib/publicStats'"), `${route} takes it from the constant`);
	}
});

// The sweep the fix was scoped by: no surface anywhere may re-acquire the
// dead host. It is the product's documentation link — a 404 there is the
// only answer a stuck reader gets.
test('no frontend source points at the retired docs host', () => {
	// This file names the dead host to assert against it, so the sweep reads
	// shipped sources only.
	let hits: string[] = [];
	try {
		hits = execFileSync(
			'grep',
			['-rl', '--exclude=*.test.ts', 'gurio.github.io', join(here, '..')],
			{ encoding: 'utf8' }
		)
			.split('\n')
			.filter(Boolean);
	} catch (e) {
		// grep exits 1 with no output when nothing matches — the passing case.
		if ((e as { status?: number }).status !== 1) throw e;
	}
	deepEqual(hits, []);
});
